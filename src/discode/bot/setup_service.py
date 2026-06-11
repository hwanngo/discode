"""Business logic for /setup command group.

Each function corresponds to a /setup subcommand and returns a typed result
dataclass — keeping Discord interaction handling in main.py separate from the
pure DB / policy logic here.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.bot.policies.admin_auth import is_guild_admin_authorized
from discode.bot.policies.tool_channel_map import (
    list_tool_channel_maps,
    remove_tool_channel_map,
    set_tool_channel_map,
)
from discode.db.models import (
    AllowedRoot,
    Guild,
    GuildAdminRolePolicy,
    GuildToolChannelMap,
    PathAlias,
)
from discode.security.paths import canonicalize

logger = logging.getLogger(__name__)

_UNAUTH_MSG = (
    "You need the configured admin role to use this command. "
    "Ask a server admin to run /setup admin-role set."
)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class InitResult:
    success: bool
    message: str


@dataclass
class MapSetResult:
    authorized: bool
    tool: str
    channel_id: str
    message: str


@dataclass
class MapListResult:
    mappings: list[GuildToolChannelMap]


@dataclass
class MapRemoveResult:
    deleted: bool
    tool: str
    message: str


@dataclass
class AdminRoleSetResult:
    success: bool
    role_id: str | None
    role_name: str | None
    message: str


@dataclass
class AdminRoleListResult:
    role_id: str | None
    role_name: str | None


@dataclass
class SyncReport:
    authorized: bool = True
    global_added: int = 0
    global_updated: int = 0
    global_removed: int = 0
    guild_added: int = 0
    guild_updated: int = 0
    guild_removed: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(dt.UTC).isoformat())
    message: str = ""


# ---------------------------------------------------------------------------
# /setup init
# ---------------------------------------------------------------------------


async def setup_init(
    session: AsyncSession,
    guild_id: str,
    invoked_by: str,
) -> InitResult:
    """Register guild if not present, then create a default policy row if none exists."""
    await session.execute(
        pg_insert(Guild)
        .values(id=guild_id, name=guild_id)
        .on_conflict_do_nothing(index_elements=["id"])
    )

    # Ensure a GuildAdminRolePolicy row exists (bootstrap row with no role set)
    stmt = (
        pg_insert(GuildAdminRolePolicy)
        .values(
            guild_id=guild_id,
            role_id=None,
            role_name_fallback=None,
            updated_by=invoked_by,
        )
        .on_conflict_do_nothing(constraint="guild_admin_role_policy_guild_id_uq")
    )
    await session.execute(stmt)
    await session.flush()

    return InitResult(
        success=True,
        message=(
            f"Guild {guild_id!r} is set up. "
            "Use /setup admin-role set to restrict who can manage Discode settings."
        ),
    )


# ---------------------------------------------------------------------------
# /setup map set / list / remove
# ---------------------------------------------------------------------------


async def setup_map_set(
    session: AsyncSession,
    guild_id: str,
    tool: str,
    channel_id: str,
    updated_by: str,
    member_roles: list[str] | None = None,
    member_role_names: list[str] | None = None,
) -> MapSetResult:
    """Upsert a tool→channel mapping, checking admin auth first."""
    authorized = await is_guild_admin_authorized(
        session,
        guild_id=guild_id,
        member_roles=member_roles or [],
        member_role_names=member_role_names,
    )
    if not authorized:
        return MapSetResult(
            authorized=False,
            tool=tool,
            channel_id=channel_id,
            message=_UNAUTH_MSG,
        )

    row = await set_tool_channel_map(session, guild_id, tool, channel_id, updated_by)
    return MapSetResult(
        authorized=True,
        tool=row.tool,
        channel_id=row.channel_id,
        message=f"Mapped tool `{tool}` → <#{channel_id}>.",
    )


async def setup_map_list(
    session: AsyncSession,
    guild_id: str,
) -> MapListResult:
    """List all tool→channel mappings for the guild."""
    mappings = await list_tool_channel_maps(session, guild_id)
    return MapListResult(mappings=mappings)


async def setup_map_remove(
    session: AsyncSession,
    guild_id: str,
    tool: str,
    member_roles: list[str] | None = None,
    member_role_names: list[str] | None = None,
) -> MapRemoveResult:
    """Remove a tool→channel mapping, checking admin auth first."""
    authorized = await is_guild_admin_authorized(
        session,
        guild_id=guild_id,
        member_roles=member_roles or [],
        member_role_names=member_role_names,
    )
    if not authorized:
        return MapRemoveResult(
            deleted=False,
            tool=tool,
            message=_UNAUTH_MSG,
        )

    deleted = await remove_tool_channel_map(session, guild_id, tool)
    if deleted:
        return MapRemoveResult(deleted=True, tool=tool, message=f"Removed mapping for `{tool}`.")
    return MapRemoveResult(
        deleted=False,
        tool=tool,
        message=f"No mapping found for tool `{tool}`.",
    )


# ---------------------------------------------------------------------------
# /setup admin-role set / list
# ---------------------------------------------------------------------------


async def setup_admin_role_set(
    session: AsyncSession,
    guild_id: str,
    role_id: str | None,
    role_name: str | None,
    updated_by: str,
    member_roles: list[str] | None = None,
    member_role_names: list[str] | None = None,
    already_authorized: bool = False,
) -> AdminRoleSetResult:
    """Upsert the admin role policy for the guild.

    ``already_authorized`` lets the caller signal that it has performed a
    bootstrap-safe authorization (e.g. verified the Discord ``manage_guild``
    permission). When True, the open admin-role DB check is skipped — this is
    required for first-touch setup, because ``is_guild_admin_authorized`` now
    fails closed when no admin role is configured.
    """
    authorized = already_authorized or await is_guild_admin_authorized(
        session,
        guild_id=guild_id,
        member_roles=member_roles or [],
        member_role_names=member_role_names,
    )
    if not authorized:
        return AdminRoleSetResult(
            success=False,
            role_id=None,
            role_name=None,
            message=_UNAUTH_MSG,
        )

    now = datetime.now(dt.UTC)
    stmt = (
        pg_insert(GuildAdminRolePolicy)
        .values(
            guild_id=guild_id,
            role_id=role_id,
            role_name_fallback=role_name,
            updated_by=updated_by,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            constraint="guild_admin_role_policy_guild_id_uq",
            set_={
                "role_id": role_id,
                "role_name_fallback": role_name,
                "updated_by": updated_by,
                "updated_at": now,
            },
        )
    )
    await session.execute(stmt)
    await session.flush()

    label = f"<@&{role_id}>" if role_id else role_name or "(none)"
    return AdminRoleSetResult(
        success=True,
        role_id=role_id,
        role_name=role_name,
        message=f"Admin role set to {label}.",
    )


async def setup_admin_role_list(
    session: AsyncSession,
    guild_id: str,
) -> AdminRoleListResult:
    """Return the current admin role policy for the guild."""
    result = await session.execute(
        select(GuildAdminRolePolicy).where(GuildAdminRolePolicy.guild_id == guild_id)
    )
    policy = result.scalars().first()

    if policy is None:
        return AdminRoleListResult(role_id=None, role_name=None)

    return AdminRoleListResult(role_id=policy.role_id, role_name=policy.role_name_fallback)


# ---------------------------------------------------------------------------
# /root add
# ---------------------------------------------------------------------------


@dataclass
class AddAllowedRootResult:
    realpath: str


async def add_allowed_root_for_user(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    guild_id: str,
    user_id: str,
    runner_host_id: str,
    path: str,
    created_by: str,
) -> AddAllowedRootResult:
    """Canonicalize path and upsert into allowed_roots.

    Raises ValueError if the path is rejected by the denylist.
    """
    realpath, deny_reason = canonicalize(path)
    if deny_reason is not None:
        raise ValueError(deny_reason)

    async with db_factory() as session:
        async with session.begin():
            await session.execute(
                pg_insert(AllowedRoot)
                .values(
                    guild_id=guild_id,
                    user_id=user_id,
                    runner_host_id=runner_host_id,
                    path=path,
                    realpath=realpath,
                    created_by=created_by,
                )
                .on_conflict_do_nothing(constraint="allowed_roots_uq")
            )

    return AddAllowedRootResult(realpath=realpath)


# ---------------------------------------------------------------------------
# /setup sync
# ---------------------------------------------------------------------------


async def setup_sync(
    session: AsyncSession,
    guild_id: str,
    invoked_by: str,
    member_roles: list[str] | None = None,
    member_role_names: list[str] | None = None,
    bot: object | None = None,
    extra_guild_ids: list[int] | None = None,
) -> SyncReport:
    """Trigger command-sync reconciliation via the hybrid reconciler.

    Parameters
    ----------
    session:
        Active DB session used for admin-auth check.
    guild_id:
        Guild that invoked the command (always included in guild sync list).
    invoked_by:
        Discord user ID of the invoking member (for audit logging).
    member_roles / member_role_names:
        Roles of the invoking member used for admin-auth check.
    bot:
        The ``DiscodeBot`` instance.  When ``None`` the function falls back to
        a zero-delta stub report (preserves backward-compat for tests that
        don't supply a bot).
    extra_guild_ids:
        Additional guild IDs to sync beyond the invoking guild.  Typically
        populated from ``bot.guilds`` by the caller.
    """
    authorized = await is_guild_admin_authorized(
        session,
        guild_id=guild_id,
        member_roles=member_roles or [],
        member_role_names=member_role_names,
    )
    if not authorized:
        return SyncReport(authorized=False, message=_UNAUTH_MSG)

    logger.info(
        "setup_sync called",
        extra={"guild_id": guild_id, "invoked_by": invoked_by},
    )

    if bot is None:
        # Backward-compat stub path (used in unit tests that don't supply a bot).
        return SyncReport(authorized=True)

    from discode.bot.command_sync import reconcile_commands

    # Build the guild list: invoking guild + any extras (deduplicated).
    all_guild_ids: list[int] = list(dict.fromkeys([int(guild_id)] + (extra_guild_ids or [])))
    sync_report = await reconcile_commands(bot, all_guild_ids)  # type: ignore[arg-type]

    return SyncReport(
        authorized=True,
        global_added=sync_report.global_added,
        global_updated=sync_report.global_updated,
        global_removed=sync_report.global_removed,
        guild_added=sync_report.guild_added,
        guild_updated=sync_report.guild_updated,
        guild_removed=sync_report.guild_removed,
        timestamp=sync_report.timestamp,
    )


# ---------------------------------------------------------------------------
# /alias set / list / remove / resolve
# ---------------------------------------------------------------------------

_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


@dataclass
class SetPathAliasResult:
    alias: str
    realpath: str


async def set_path_alias(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    guild_id: str,
    alias: str,
    path: str,
    runner_host_id: str,
    created_by: str,
) -> SetPathAliasResult:
    """Register or replace a guild-shared root path alias.

    1. Validates alias format (^[a-z][a-z0-9_-]{0,63}$).
    2. Canonicalizes the path through the security denylist.
    3. Upserts (guild_id, alias) -> (path, realpath) into path_aliases.
    4. Upserts (guild_id, "", runner_host_id, realpath) into allowed_roots
       so any member of the guild can use this path.
    """
    if not _ALIAS_RE.match(alias):
        raise ValueError(f"invalid_alias_format: {alias!r}")

    realpath, deny_reason = canonicalize(path)
    if deny_reason is not None:
        raise ValueError(deny_reason)

    async with db_factory() as session:
        async with session.begin():
            await session.execute(
                pg_insert(PathAlias)
                .values(
                    guild_id=guild_id,
                    alias=alias,
                    path=path,
                    realpath=realpath,
                    created_by=created_by,
                )
                .on_conflict_do_update(
                    constraint="path_aliases_uq",
                    set_={
                        "path": path,
                        "realpath": realpath,
                        "created_by": created_by,
                    },
                )
            )
            await session.execute(
                pg_insert(AllowedRoot)
                .values(
                    guild_id=guild_id,
                    user_id="",
                    runner_host_id=runner_host_id,
                    path=path,
                    realpath=realpath,
                    created_by=created_by,
                )
                .on_conflict_do_nothing(constraint="allowed_roots_uq")
            )

    return SetPathAliasResult(alias=alias, realpath=realpath)


async def list_path_aliases(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    guild_id: str,
) -> list[PathAlias]:
    """Return all aliases for the guild, alphabetical by alias."""
    async with db_factory() as session:
        result = await session.execute(
            select(PathAlias).where(PathAlias.guild_id == guild_id).order_by(PathAlias.alias)
        )
        return list(result.scalars().all())


async def remove_path_alias(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    guild_id: str,
    alias: str,
) -> bool:
    """Delete the alias. Returns True if deleted, False if not found.

    Does NOT remove the corresponding allowed_roots row — admins who want to
    revoke guild access should call /root remove explicitly.
    """
    async with db_factory() as session:
        async with session.begin():
            cursor = await session.execute(
                delete(PathAlias).where(
                    PathAlias.guild_id == guild_id,
                    PathAlias.alias == alias,
                )
            )
        return bool(cursor.rowcount > 0)


async def resolve_alias_or_path(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    guild_id: str,
    raw_cwd: str,
) -> str:
    """Resolve `raw_cwd` to a filesystem path.

    - If `raw_cwd` is absolute (starts with '/'), return it unchanged.
    - Otherwise, look up `raw_cwd` as an alias in path_aliases for the guild.
      Returns the alias's realpath. Raises ValueError("unknown_alias: <name>")
      if no alias matches.
    """
    if raw_cwd.startswith("/"):
        return raw_cwd

    async with db_factory() as session:
        result = await session.execute(
            select(PathAlias.realpath).where(
                PathAlias.guild_id == guild_id,
                PathAlias.alias == raw_cwd,
            )
        )
        realpath = result.scalar_one_or_none()
    if realpath is None:
        raise ValueError(f"unknown_alias: {raw_cwd}")
    return realpath
