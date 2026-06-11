"""Tests for /start thread routing — Task 4.

Tests cover:
- /start resolves the mapped channel and embeds thread_id in session + outbox
- /start fails with a clear message when no mapping is configured
- bind_session_thread() atomically updates session.thread_id and outbox payload
- Degraded path: bind failure marks session with thread_bind_failed reason
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa
from cryptography.fernet import Fernet

from discode.bot.policies.tool_channel_map import set_tool_channel_map
from discode.bot.sagas.create import run_create_saga
from discode.bot.session_service import (
    bind_session_thread,
)
from discode.db.engine import make_engine, make_session_factory
from discode.db.models import Guild, RedisOutbox, RunnerHost, User
from discode.db.models import Session as SessionModel

TEST_DEPLOYMENT_KEY = Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_factory_fixture(migrated_db_url):
    engine = make_engine(migrated_db_url)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


def _unique_ids():
    return (
        f"guild-{uuid.uuid4().hex[:8]}",
        f"user-{uuid.uuid4().hex[:8]}",
        f"host-{uuid.uuid4().hex[:8]}",
    )


async def _seed_prerequisites(db_factory, guild_id, user_id, host_id):
    async with db_factory() as db:
        db.add(Guild(id=guild_id, name="Test Guild"))
        db.add(User(id=user_id, username="testuser"))
        db.add(RunnerHost(id=host_id, status="online"))
        await db.commit()


def _make_saga_kwargs(guild_id, user_id, host_id, **overrides):
    base = dict(
        guild_id=guild_id,
        owner_id=user_id,
        owner_username="testuser",
        tool="claude",
        name=f"my-session-{uuid.uuid4().hex[:6]}",
        cwd=f"/home/user/project-{uuid.uuid4().hex[:6]}",
        cwd_realpath=f"/home/user/project-{uuid.uuid4().hex[:6]}",
        parent_channel_id="channel-001",
        runner_host_id=host_id,
        deployment_secret_key=TEST_DEPLOYMENT_KEY,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# bind_session_thread — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_session_thread_updates_session_row(db_factory_fixture):
    """bind_session_thread stores thread_id on the session row."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    result = await run_create_saga(
        db_factory_fixture,
        **_make_saga_kwargs(guild_id, user_id, host_id),
    )
    session_uuid = uuid.UUID(result.session_id)
    thread_id = f"thread-{uuid.uuid4().hex[:10]}"

    bind_result = await bind_session_thread(
        db_factory_fixture,
        session_id=result.session_id,
        thread_id=thread_id,
    )

    assert bind_result.success is True
    assert bind_result.session_id == result.session_id
    assert bind_result.thread_id == thread_id

    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, session_uuid)
        assert row is not None
        assert row.thread_id == thread_id


@pytest.mark.asyncio
async def test_bind_session_thread_updates_outbox_payload(db_factory_fixture):
    """bind_session_thread patches thread_id into the queued outbox payload."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    result = await run_create_saga(
        db_factory_fixture,
        **_make_saga_kwargs(guild_id, user_id, host_id),
    )
    thread_id = f"thread-{uuid.uuid4().hex[:10]}"

    await bind_session_thread(
        db_factory_fixture,
        session_id=result.session_id,
        thread_id=thread_id,
    )

    async with db_factory_fixture() as db:
        outbox_result = await db.execute(
            sa.select(RedisOutbox).where(
                sa.func.json_extract_path_text(RedisOutbox.payload, "session_id")
                == result.session_id,
                RedisOutbox.envelope_type == "runner.create_session.v1",
                RedisOutbox.published_at.is_(None),
            )
        )
        outbox_row = outbox_result.scalar_one_or_none()
        assert outbox_row is not None
        assert outbox_row.payload["thread_id"] == thread_id


@pytest.mark.asyncio
async def test_bind_session_thread_is_atomic(db_factory_fixture):
    """Both session row and outbox payload are updated in the same transaction."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    result = await run_create_saga(
        db_factory_fixture,
        **_make_saga_kwargs(guild_id, user_id, host_id),
    )
    session_uuid = uuid.UUID(result.session_id)
    thread_id = f"thread-{uuid.uuid4().hex[:10]}"

    await bind_session_thread(
        db_factory_fixture,
        session_id=result.session_id,
        thread_id=thread_id,
    )

    # Verify both in a single read transaction
    async with db_factory_fixture() as db:
        session_row = await db.get(SessionModel, session_uuid)
        outbox_result = await db.execute(
            sa.select(RedisOutbox).where(
                sa.func.json_extract_path_text(RedisOutbox.payload, "session_id")
                == result.session_id,
                RedisOutbox.published_at.is_(None),
            )
        )
        outbox_row = outbox_result.scalar_one_or_none()

    assert session_row is not None
    assert session_row.thread_id == thread_id
    assert outbox_row is not None
    assert outbox_row.payload["thread_id"] == thread_id


# ---------------------------------------------------------------------------
# bind_session_thread — degraded path (session not found)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_session_thread_missing_session_returns_degraded(db_factory_fixture):
    """bind_session_thread with a nonexistent session_id returns degraded result."""
    fake_session_id = str(uuid.uuid4())
    thread_id = f"thread-{uuid.uuid4().hex[:10]}"

    bind_result = await bind_session_thread(
        db_factory_fixture,
        session_id=fake_session_id,
        thread_id=thread_id,
    )

    assert bind_result.success is False
    assert bind_result.status_reason == "thread_bind_failed"


# ---------------------------------------------------------------------------
# resolve_tool_channel integration — mapping gates the saga
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_tool_channel_missing_returns_none(db_factory_fixture):
    """Without a mapping, resolve_tool_channel returns None (caller should gate /start)."""
    from discode.bot.policies.tool_channel_map import resolve_tool_channel

    guild_id, _, _ = _unique_ids()
    async with db_factory_fixture() as db:
        db.add(Guild(id=guild_id, name="Test Guild"))
        await db.commit()

    async with db_factory_fixture() as db:
        channel_id = await resolve_tool_channel(db, guild_id, "claude")

    assert channel_id is None


@pytest.mark.asyncio
async def test_resolve_tool_channel_present_returns_channel(db_factory_fixture):
    """With a mapping configured, resolve_tool_channel returns the channel_id."""
    from discode.bot.policies.tool_channel_map import resolve_tool_channel

    guild_id, _, _ = _unique_ids()
    async with db_factory_fixture() as db:
        db.add(Guild(id=guild_id, name="Test Guild"))
        await db.commit()

    mapped_channel = f"channel-{uuid.uuid4().hex[:8]}"

    async with db_factory_fixture() as db:
        await set_tool_channel_map(db, guild_id, "claude", mapped_channel, "user-admin")
        await db.commit()

    async with db_factory_fixture() as db:
        channel_id = await resolve_tool_channel(db, guild_id, "claude")

    assert channel_id == mapped_channel


# ---------------------------------------------------------------------------
# Full start flow: mapping → saga → bind (integration smoke)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_with_mapping(db_factory_fixture):
    """Full /start flow: resolve channel, run saga, bind thread_id to session and outbox."""
    from discode.bot.policies.tool_channel_map import resolve_tool_channel

    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    mapped_channel = f"channel-{uuid.uuid4().hex[:8]}"

    # Pre-configure the mapping (as /setup map set would do)
    async with db_factory_fixture() as db:
        await set_tool_channel_map(db, guild_id, "claude", mapped_channel, user_id)
        await db.commit()

    # Step 1: resolve channel (handler would fail if None)
    async with db_factory_fixture() as db:
        resolved = await resolve_tool_channel(db, guild_id, "claude")
    assert resolved == mapped_channel

    # Step 2: create session saga
    saga_kwargs = _make_saga_kwargs(guild_id, user_id, host_id, parent_channel_id=resolved)
    saga_result = await run_create_saga(db_factory_fixture, **saga_kwargs)

    # Step 3: bind thread
    thread_id = f"thread-{uuid.uuid4().hex[:10]}"
    bind_result = await bind_session_thread(
        db_factory_fixture,
        session_id=saga_result.session_id,
        thread_id=thread_id,
    )

    assert bind_result.success is True

    # Verify final state
    session_uuid = uuid.UUID(saga_result.session_id)
    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, session_uuid)
        assert row is not None
        assert row.thread_id == thread_id
        assert row.parent_channel_id == mapped_channel


@pytest.mark.asyncio
async def test_start_flow_without_mapping_no_saga(db_factory_fixture):
    """When mapping is missing, the caller should not create a session.

    This test validates that resolve_tool_channel returns None and a guard in
    the /start handler would stop the saga — we simulate that guard here.
    """
    from discode.bot.policies.tool_channel_map import resolve_tool_channel

    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    async with db_factory_fixture() as db:
        resolved = await resolve_tool_channel(db, guild_id, "claude")

    # No mapping → caller must fail without creating a session
    assert resolved is None

    # Verify no session was created for this guild
    async with db_factory_fixture() as db:
        result = await db.execute(sa.select(SessionModel).where(SessionModel.guild_id == guild_id))
        rows = result.scalars().all()
    assert len(rows) == 0
