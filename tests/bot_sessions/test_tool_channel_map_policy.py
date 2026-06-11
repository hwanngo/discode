from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from discode.bot.policies.tool_channel_map import (
    get_tool_channel_map,
    list_tool_channel_maps,
    remove_tool_channel_map,
    resolve_tool_channel,
    set_tool_channel_map,
)
from discode.db.engine import make_engine, make_session_factory
from discode.db.models import Guild


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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_and_get_tool_channel_map(db_factory_fixture):
    """set_tool_channel_map inserts a row; get_tool_channel_map retrieves it."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        await set_tool_channel_map(session, guild_id, "claude", "channel-111", "user-abc")
        await session.commit()

    async with db_factory_fixture() as session:
        fetched = await get_tool_channel_map(session, guild_id, "claude")

    assert fetched is not None
    assert fetched.guild_id == guild_id
    assert fetched.tool == "claude"
    assert fetched.channel_id == "channel-111"
    assert fetched.updated_by == "user-abc"


@pytest.mark.asyncio
async def test_upsert_updates_existing(db_factory_fixture):
    """Calling set_tool_channel_map twice updates channel_id (upsert)."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        await set_tool_channel_map(session, guild_id, "claude", "channel-111", "user-abc")
        await session.commit()

    async with db_factory_fixture() as session:
        await set_tool_channel_map(session, guild_id, "claude", "channel-999", "user-xyz")
        await session.commit()

    async with db_factory_fixture() as session:
        fetched = await get_tool_channel_map(session, guild_id, "claude")

    assert fetched is not None
    assert fetched.channel_id == "channel-999"
    assert fetched.updated_by == "user-xyz"


@pytest.mark.asyncio
async def test_resolve_tool_channel_returns_id(db_factory_fixture):
    """resolve_tool_channel returns the channel_id string when mapping exists."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        await set_tool_channel_map(session, guild_id, "codex", "channel-42", "user-abc")
        await session.commit()

    async with db_factory_fixture() as session:
        channel_id = await resolve_tool_channel(session, guild_id, "codex")

    assert channel_id == "channel-42"


@pytest.mark.asyncio
async def test_resolve_tool_channel_missing_returns_none(db_factory_fixture):
    """resolve_tool_channel returns None when no mapping exists."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        channel_id = await resolve_tool_channel(session, guild_id, "opencode")

    assert channel_id is None


@pytest.mark.asyncio
async def test_list_tool_channel_maps(db_factory_fixture):
    """list_tool_channel_maps returns all mappings for a guild."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        await set_tool_channel_map(session, guild_id, "claude", "channel-1", "user-a")
        await set_tool_channel_map(session, guild_id, "codex", "channel-2", "user-a")
        await session.commit()

    async with db_factory_fixture() as session:
        maps = await list_tool_channel_maps(session, guild_id)

    tools = {m.tool for m in maps}
    assert "claude" in tools
    assert "codex" in tools
    assert len(maps) == 2


@pytest.mark.asyncio
async def test_remove_tool_channel_map(db_factory_fixture):
    """remove_tool_channel_map deletes the row and returns True; second call returns False."""
    guild_id = _guild_id()
    await _seed_guild(db_factory_fixture, guild_id)

    async with db_factory_fixture() as session:
        await set_tool_channel_map(session, guild_id, "claude", "channel-1", "user-a")
        await session.commit()

    async with db_factory_fixture() as session:
        deleted = await remove_tool_channel_map(session, guild_id, "claude")
        await session.commit()

    assert deleted is True

    async with db_factory_fixture() as session:
        not_deleted = await remove_tool_channel_map(session, guild_id, "claude")
        await session.commit()

    assert not_deleted is False
