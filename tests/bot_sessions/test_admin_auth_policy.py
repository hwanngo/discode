from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from discode.bot.policies.admin_auth import is_guild_admin_authorized
from discode.db.engine import make_engine, make_session_factory
from discode.db.models import Guild, GuildAdminRolePolicy


@pytest_asyncio.fixture
async def db_factory_fixture(migrated_db_url):
    engine = make_engine(migrated_db_url)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


def _guild_id() -> str:
    return f"guild-{uuid.uuid4().hex[:8]}"


async def _seed_guild(db_factory, guild_id: str) -> None:
    async with db_factory() as db:
        db.add(Guild(id=guild_id, name="Test Guild"))
        await db.commit()


async def _seed_policy(
    db_factory,
    guild_id: str,
    role_id: str | None = None,
    role_name_fallback: str | None = None,
) -> None:
    async with db_factory() as db:
        db.add(
            GuildAdminRolePolicy(
                guild_id=guild_id,
                role_id=role_id,
                role_name_fallback=role_name_fallback,
                updated_by="test-user",
            )
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_auth_by_role_id(db_factory_fixture):
    """Policy row with role_id exists and user has that role -> True."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)
    await _seed_policy(db_factory_fixture, guild_id, role_id="role-admin-123")

    async with db_factory_fixture() as session:
        result = await is_guild_admin_authorized(
            session,
            guild_id,
            member_roles=["role-regular", "role-admin-123"],
        )

    assert result is True


@pytest.mark.asyncio
async def test_admin_auth_role_id_missing(db_factory_fixture):
    """Policy row with role_id exists but user lacks it -> False."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)
    await _seed_policy(db_factory_fixture, guild_id, role_id="role-admin-123")

    async with db_factory_fixture() as session:
        result = await is_guild_admin_authorized(
            session,
            guild_id,
            member_roles=["role-regular", "role-other"],
        )

    assert result is False


@pytest.mark.asyncio
async def test_admin_auth_by_role_name_fallback(db_factory_fixture):
    """Policy has no role_id but role_name_fallback matches a name in member_role_names -> True."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)
    await _seed_policy(db_factory_fixture, guild_id, role_name_fallback="Server Admin")

    async with db_factory_fixture() as session:
        result = await is_guild_admin_authorized(
            session,
            guild_id,
            member_roles=["role-123"],
            member_role_names=["Member", "Server Admin"],
        )

    assert result is True


@pytest.mark.asyncio
async def test_admin_auth_bootstrap_no_policy(db_factory_fixture):
    """No policy row exists for guild -> False (fail closed; bootstrap is gated
    on manage_guild by callers, not by this open check)."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)
    # intentionally do NOT seed a policy

    async with db_factory_fixture() as session:
        result = await is_guild_admin_authorized(
            session,
            guild_id,
            member_roles=["role-xyz"],
        )

    assert result is False


@pytest.mark.asyncio
async def test_admin_auth_policy_exists_no_match(db_factory_fixture):
    """Policy exists with role_name_fallback but user role names don't match -> False."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)
    await _seed_policy(db_factory_fixture, guild_id, role_name_fallback="Server Admin")

    async with db_factory_fixture() as session:
        result = await is_guild_admin_authorized(
            session,
            guild_id,
            member_roles=["role-123"],
            member_role_names=["Member", "Moderator"],
        )

    assert result is False


@pytest.mark.asyncio
async def test_admin_auth_degenerate_policy_denies(db_factory_fixture):
    """Policy row exists but role_id=None and role_name_fallback=None -> False.

    A configured (but role-less) policy is enforced, not treated as fail-open
    bootstrap. This prevents self-promotion in guilds that ran /setup init but
    have not yet set an admin role.
    """
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)
    await _seed_policy(db_factory_fixture, guild_id, role_id=None, role_name_fallback=None)

    async with db_factory_fixture() as session:
        result = await is_guild_admin_authorized(
            session,
            guild_id,
            member_roles=["role-admin-123"],
            member_role_names=["Admin"],
        )

    assert result is False
