from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import text

from discode.db.engine import make_engine, make_session_factory
from discode.db.models import Guild, RedisOutbox, RunnerHost, User
from discode.db.models import Session as SessionModel
from discode.janitor.timeouts import (
    sweep_archive_timeout,
    sweep_creating_watchdog,
    sweep_idle_timeout,
    sweep_resuming_watchdog,
)


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


async def _insert_session(db_factory, guild_id, host_id, status, tmux_suffix=None) -> str:
    """Insert a minimal session row and return its ID as str."""
    sid = uuid.uuid4()
    async with db_factory() as db:
        async with db.begin():
            await db.execute(
                text("""
                    INSERT INTO sessions (
                        id, guild_id, tool, name, cwd, cwd_realpath,
                        hook_secret_hash, status, host_id
                    ) VALUES (
                        :id, :guild_id, 'claude', 'test', '/tmp', '/tmp',
                        'hash', :status, :host_id
                    )
                """),
                {
                    "id": str(sid),
                    "guild_id": guild_id,
                    "status": status,
                    "host_id": host_id,
                },
            )
    return str(sid)


@pytest.mark.asyncio
async def test_sweep_creating_watchdog_transitions_old_session(db_factory_fixture):
    """sweep_creating_watchdog transitions old 'creating' sessions to 'failed'."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)
    session_id = await _insert_session(db_factory_fixture, guild_id, host_id, "creating")

    # Age the session past the timeout
    async with db_factory_fixture() as db:
        async with db.begin():
            await db.execute(
                text(
                    "UPDATE sessions SET created_at = now() - interval '2 minutes' WHERE id = :sid"
                ),
                {"sid": session_id},
            )

    count = await sweep_creating_watchdog(db_factory_fixture)
    assert count >= 1

    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, uuid.UUID(session_id))
        assert row is not None
        assert row.status == "failed"
        assert row.status_reason == "creation_timeout"


@pytest.mark.asyncio
async def test_sweep_creating_watchdog_emits_outbox_notice(db_factory_fixture):
    """sweep_creating_watchdog emits an outbox notice for the failed session."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)
    session_id = await _insert_session(db_factory_fixture, guild_id, host_id, "creating")

    async with db_factory_fixture() as db:
        async with db.begin():
            await db.execute(
                text(
                    "UPDATE sessions SET created_at = now() - interval '2 minutes' WHERE id = :sid"
                ),
                {"sid": session_id},
            )

    await sweep_creating_watchdog(db_factory_fixture)

    async with db_factory_fixture() as db:
        result = await db.execute(
            sa.select(RedisOutbox).where(
                sa.func.json_extract_path_text(RedisOutbox.payload, "session_id") == session_id
            )
        )
        outbox_row = result.scalar_one_or_none()
        assert outbox_row is not None
        assert outbox_row.envelope_type == "discord.system_notice.v1"
        assert outbox_row.payload["notice_type"] == "session_create_failed"


@pytest.mark.asyncio
async def test_sweep_creating_watchdog_does_not_touch_recent_sessions(db_factory_fixture):
    """sweep_creating_watchdog does not touch recent 'creating' sessions."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)
    session_id = await _insert_session(db_factory_fixture, guild_id, host_id, "creating")

    # Session is recent — do NOT age it

    await sweep_creating_watchdog(db_factory_fixture)

    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, uuid.UUID(session_id))
        assert row is not None
        assert row.status == "creating"


@pytest.mark.asyncio
async def test_sweep_resuming_watchdog_transitions_old_resuming_sessions(db_factory_fixture):
    """sweep_resuming_watchdog transitions old 'resuming' sessions to 'orphaned'."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)
    session_id = await _insert_session(db_factory_fixture, guild_id, host_id, "resuming")

    async with db_factory_fixture() as db:
        async with db.begin():
            await db.execute(
                text(
                    "UPDATE sessions SET last_activity_at = now()"
                    " - interval '10 minutes' WHERE id = :sid"
                ),
                {"sid": session_id},
            )

    count = await sweep_resuming_watchdog(db_factory_fixture)
    assert count >= 1

    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, uuid.UUID(session_id))
        assert row is not None
        assert row.status == "orphaned"
        assert row.status_reason == "resume_stuck"


@pytest.mark.asyncio
async def test_sweep_idle_timeout_transitions_running_to_idle(db_factory_fixture):
    """sweep_idle_timeout transitions old 'running' sessions to 'idle'."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)
    session_id = await _insert_session(db_factory_fixture, guild_id, host_id, "running")

    async with db_factory_fixture() as db:
        async with db.begin():
            await db.execute(
                text(
                    "UPDATE sessions SET last_activity_at = now()"
                    " - interval '2 hours' WHERE id = :sid"
                ),
                {"sid": session_id},
            )

    count = await sweep_idle_timeout(db_factory_fixture, idle_timeout_minutes=60)
    assert count >= 1

    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, uuid.UUID(session_id))
        assert row is not None
        assert row.status == "idle"


@pytest.mark.asyncio
async def test_sweep_archive_timeout_transitions_idle_to_archived(db_factory_fixture):
    """sweep_archive_timeout: idle sessions → archived and emits archive envelope."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)
    session_id = await _insert_session(db_factory_fixture, guild_id, host_id, "idle")

    async with db_factory_fixture() as db:
        async with db.begin():
            await db.execute(
                text(
                    "UPDATE sessions SET last_activity_at = now()"
                    " - interval '3 hours' WHERE id = :sid"
                ),
                {"sid": session_id},
            )

    count = await sweep_archive_timeout(db_factory_fixture, archive_timeout_minutes=120)
    assert count >= 1

    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, uuid.UUID(session_id))
        assert row is not None
        assert row.status == "archived"
        assert row.archived_at is not None
        assert row.thread_archive_requested_at is not None

        result = await db.execute(
            sa.select(RedisOutbox).where(
                sa.func.json_extract_path_text(RedisOutbox.payload, "session_id") == session_id
            )
        )
        outbox_row = result.scalar_one_or_none()
        assert outbox_row is not None
        assert outbox_row.envelope_type == "discord.archive_thread.v1"
