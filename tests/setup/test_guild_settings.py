from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from discode.db.accessors.guild_settings import get_setting, set_setting
from discode.db.engine import make_engine, make_session_factory
from discode.db.models import Guild


@pytest_asyncio.fixture
async def seeded_guild(db_session: AsyncSession) -> Guild:
    guild = Guild(id=f"gs-guild-{uuid.uuid4().hex[:8]}", name="Test Guild")
    db_session.add(guild)
    await db_session.commit()
    return guild


@pytest.mark.asyncio
async def test_get_setting_returns_default_when_missing(
    db_session: AsyncSession, seeded_guild: Guild
) -> None:
    result = await get_setting(db_session, seeded_guild.id, "nonexistent_key", default="fallback")
    assert result == "fallback"


@pytest.mark.asyncio
async def test_set_and_get_setting(db_session: AsyncSession, seeded_guild: Guild) -> None:
    await set_setting(db_session, seeded_guild.id, "auto_approve:123", True)
    result = await get_setting(db_session, seeded_guild.id, "auto_approve:123")
    assert result is True


@pytest.mark.asyncio
async def test_set_setting_updates_existing(db_session: AsyncSession, seeded_guild: Guild) -> None:
    await set_setting(db_session, seeded_guild.id, "model:456", "gpt-4")
    await set_setting(db_session, seeded_guild.id, "model:456", "claude-3")
    result = await get_setting(db_session, seeded_guild.id, "model:456")
    assert result == "claude-3"


@pytest.mark.asyncio
async def test_multiple_keys_coexist(db_session: AsyncSession, seeded_guild: Guild) -> None:
    await set_setting(db_session, seeded_guild.id, "passthrough:thread1", True)
    await set_setting(db_session, seeded_guild.id, "autowork:channel2", False)
    val1 = await get_setting(db_session, seeded_guild.id, "passthrough:thread1")
    val2 = await get_setting(db_session, seeded_guild.id, "autowork:channel2")
    assert val1 is True
    assert val2 is False


@pytest.mark.asyncio
async def test_concurrent_writers_no_lost_update(migrated_db_url: str) -> None:
    """Two concurrent writers of different keys must both survive (no lost update).

    Each writer commits in its own transaction. Because set_setting merges
    server-side rather than read-modify-writing the whole blob in Python, the
    second commit preserves the first writer's key.
    """
    engine = make_engine(migrated_db_url)
    try:
        factory = make_session_factory(engine)
        guild_id = f"gs-conc-{uuid.uuid4().hex[:8]}"

        async with factory() as setup:
            async with setup.begin():
                setup.add(Guild(id=guild_id, name="Conc Guild"))

        # Writer A sets key_a; Writer B sets key_b. Commit A, then B (separate txns).
        async with factory() as a:
            async with a.begin():
                await set_setting(a, guild_id, "key_a", "A")
        async with factory() as b:
            async with b.begin():
                await set_setting(b, guild_id, "key_b", "B")

        async with factory() as check:
            assert await get_setting(check, guild_id, "key_a") == "A"
            assert await get_setting(check, guild_id, "key_b") == "B"
    finally:
        await engine.dispose()
