"""Tests for /setup command business logic via setup_service.py."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from discode.bot.setup_service import (
    AdminRoleSetResult,
    InitResult,
    MapListResult,
    MapRemoveResult,
    MapSetResult,
    SyncReport,
    setup_admin_role_list,
    setup_admin_role_set,
    setup_init,
    setup_map_list,
    setup_map_remove,
    setup_map_set,
    setup_sync,
)
from discode.db.engine import make_engine, make_session_factory
from discode.db.models import Guild, GuildAdminRolePolicy

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
# setup_init tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_init_confirms_registered_guild(db_factory_fixture):
    """setup_init on a registered guild returns success."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        result = await setup_init(session, guild_id=guild_id, invoked_by="user-1")

    assert isinstance(result, InitResult)
    assert result.success is True
    assert guild_id in result.message


@pytest.mark.asyncio
async def test_setup_init_unregistered_guild_auto_registers(db_factory_fixture):
    """setup_init on an unknown guild auto-registers it and returns success."""
    guild_id = _guild_id()
    # deliberately NOT seeding the guild

    async with db_factory_fixture() as session:
        result = await setup_init(session, guild_id=guild_id, invoked_by="user-1")

    assert isinstance(result, InitResult)
    assert result.success is True


@pytest.mark.asyncio
async def test_setup_init_idempotent(db_factory_fixture):
    """Calling setup_init twice on the same guild succeeds both times (on_conflict_do_nothing)."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        result1 = await setup_init(session, guild_id=guild_id, invoked_by="user-1")
        await session.commit()

    async with db_factory_fixture() as session:
        result2 = await setup_init(session, guild_id=guild_id, invoked_by="user-2")
        await session.commit()

    assert result1.success is True
    assert result2.success is True


# ---------------------------------------------------------------------------
# setup_map_set / setup_map_list / setup_map_remove tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_map_upserts_mapping(db_factory_fixture):
    """setup_map_set upserts a tool->channel mapping and returns it."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        result = await setup_map_set(
            session,
            guild_id=guild_id,
            tool="claude",
            channel_id="chan-999",
            updated_by="user-1",
        )
        await session.commit()

    assert isinstance(result, MapSetResult)
    assert result.tool == "claude"
    assert result.channel_id == "chan-999"


@pytest.mark.asyncio
async def test_setup_map_upsert_updates_existing(db_factory_fixture):
    """Calling setup_map_set twice updates the channel (upsert behaviour)."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        await setup_map_set(session, guild_id, "claude", "chan-1", "user-1")
        await session.commit()

    async with db_factory_fixture() as session:
        result = await setup_map_set(session, guild_id, "claude", "chan-2", "user-2")
        await session.commit()

    assert result.channel_id == "chan-2"


@pytest.mark.asyncio
async def test_setup_map_list_returns_all(db_factory_fixture):
    """setup_map_list returns all mappings for the guild."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        await setup_map_set(session, guild_id, "claude", "chan-1", "user-1")
        await setup_map_set(session, guild_id, "codex", "chan-2", "user-1")
        await session.commit()

    async with db_factory_fixture() as session:
        result = await setup_map_list(session, guild_id=guild_id)

    assert isinstance(result, MapListResult)
    tools = {m.tool for m in result.mappings}
    assert "claude" in tools
    assert "codex" in tools


@pytest.mark.asyncio
async def test_setup_map_remove_deletes_mapping(db_factory_fixture):
    """setup_map_remove removes an existing mapping and confirms deletion."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        await setup_map_set(session, guild_id, "opencode", "chan-3", "user-1")
        await session.commit()

    async with db_factory_fixture() as session:
        result = await setup_map_remove(session, guild_id=guild_id, tool="opencode")
        await session.commit()

    assert isinstance(result, MapRemoveResult)
    assert result.deleted is True


@pytest.mark.asyncio
async def test_setup_map_remove_nonexistent_returns_not_found(db_factory_fixture):
    """setup_map_remove on missing tool returns deleted=False."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        result = await setup_map_remove(session, guild_id=guild_id, tool="ghost-tool")
        await session.commit()

    assert result.deleted is False


# ---------------------------------------------------------------------------
# setup_admin_role_set / setup_admin_role_list tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_admin_role_set(db_factory_fixture):
    """setup_admin_role_set stores the role policy and returns confirmation."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        result = await setup_admin_role_set(
            session,
            guild_id=guild_id,
            role_id="role-777",
            role_name=None,
            updated_by="user-1",
        )
        await session.commit()

    assert isinstance(result, AdminRoleSetResult)
    assert result.role_id == "role-777"
    assert result.success is True


@pytest.mark.asyncio
async def test_setup_admin_role_list_shows_policy(db_factory_fixture):
    """setup_admin_role_list returns the current admin role policy for the guild."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)
    await _seed_policy(db_factory_fixture, guild_id, role_id="role-abc")

    async with db_factory_fixture() as session:
        result = await setup_admin_role_list(session, guild_id=guild_id)

    assert result.role_id == "role-abc"


@pytest.mark.asyncio
async def test_setup_admin_role_list_no_policy(db_factory_fixture):
    """setup_admin_role_list returns None values when no policy is configured."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        result = await setup_admin_role_list(session, guild_id=guild_id)

    assert result.role_id is None
    assert result.role_name is None


# ---------------------------------------------------------------------------
# setup_map_requires_admin (integration guard — tested via service flag)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_map_requires_admin(db_factory_fixture):
    """When the caller is not authorized, setup_map_set returns an auth-error result."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)
    # Set a strict policy that will reject our member
    await _seed_policy(db_factory_fixture, guild_id, role_id="role-admin-only")

    async with db_factory_fixture() as session:
        result = await setup_map_set(
            session,
            guild_id=guild_id,
            tool="claude",
            channel_id="chan-x",
            updated_by="user-noadmin",
            member_roles=["role-nobody"],  # does NOT include role-admin-only
        )

    assert isinstance(result, MapSetResult)
    assert result.authorized is False
    assert "admin" in result.message.lower()


@pytest.mark.asyncio
async def test_setup_sync_returns_report(db_factory_fixture):
    """setup_sync returns a SyncReport with the expected structure."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        report = await setup_sync(session, guild_id=guild_id, invoked_by="user-1")

    assert isinstance(report, SyncReport)
    assert hasattr(report, "global_added")
    assert hasattr(report, "global_updated")
    assert hasattr(report, "global_removed")
    assert hasattr(report, "guild_added")
    assert hasattr(report, "guild_updated")
    assert hasattr(report, "guild_removed")
    assert hasattr(report, "timestamp")
