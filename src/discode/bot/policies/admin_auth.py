from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from discode.db.models import GuildAdminRolePolicy


async def is_guild_admin_authorized(
    session: AsyncSession,
    guild_id: str,
    member_roles: list[str],
    member_role_names: list[str] | None = None,
) -> bool:
    """Return True if the member is authorized as a guild admin.

    This helper FAILS CLOSED — it never grants access on the basis of an absent
    or unconfigured policy. The genuine first-touch bootstrap (where a guild has
    not yet set an admin role) is handled exclusively by callers that
    independently require the Discord ``manage_guild`` permission (e.g.
    ``/setup init`` and ``/setup admin-role set``).

    Authorization chain:
    1. Look up GuildAdminRolePolicy for guild_id.
    2. If NO policy row exists at all: return False (no fail-open here).
    3. If policy has role_id: check if role_id is in member_roles.
    4. Else if policy has role_name_fallback: check if it matches any name in
       member_role_names.
    5. If policy exists but no role is configured (role_id and
       role_name_fallback both None): return False (configured => enforce).
    """
    result = await session.execute(
        select(GuildAdminRolePolicy).where(GuildAdminRolePolicy.guild_id == guild_id)
    )
    policy = result.scalars().first()

    # No policy configured yet — DENY. Bootstrap is gated on manage_guild by the
    # caller, not by this open check.
    if policy is None:
        return False

    # Primary check: explicit role ID
    if policy.role_id is not None:
        return policy.role_id in member_roles

    # Fallback check: role name comparison
    if policy.role_name_fallback is not None:
        names = member_role_names or []
        return policy.role_name_fallback in names

    # Policy row exists but no role configured yet — DENY (enforce, don't fail open).
    return False
