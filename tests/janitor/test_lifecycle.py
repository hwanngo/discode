from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import text

from discode.db.engine import make_engine, make_session_factory
from discode.db.models import Guild, RedisOutbox, RunnerHost, User
from discode.db.models import Session as SessionModel
from discode.janitor.lifecycle import sweep_runner_heartbeat_orphans


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


async def _seed_prerequisites(db_factory, guild_id, user_id, host_id, host_status="online"):
    async with db_factory() as db:
        db.add(Guild(id=guild_id, name="Test Guild"))
        db.add(User(id=user_id, username="testuser"))
        db.add(RunnerHost(id=host_id, status=host_status))
        await db.commit()


async def _insert_session(db_factory, guild_id, host_id, status) -> str:
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
async def test_sweep_runner_heartbeat_orphans_marks_sessions_failed(db_factory_fixture):
    """sweep_runner_heartbeat_orphans marks sessions as 'failed' when runner heartbeat is stale."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id, host_status="online")

    session_id = await _insert_session(db_factory_fixture, guild_id, host_id, "running")

    # Age the runner heartbeat
    async with db_factory_fixture() as db:
        async with db.begin():
            await db.execute(
                text(
                    "UPDATE runner_hosts SET last_heartbeat_at ="
                    " now() - interval '30 hours' WHERE id = :hid"
                ),
                {"hid": host_id},
            )

    count = await sweep_runner_heartbeat_orphans(db_factory_fixture, orphan_recovery_ttl_hours=24)
    assert count >= 1

    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, uuid.UUID(session_id))
        assert row is not None
        assert row.status == "failed"
        assert row.status_reason == "runner_lost"

    # Runner host should now be offline
    async with db_factory_fixture() as db:
        host_row = await db.get(RunnerHost, host_id)
        assert host_row is not None
        assert host_row.status == "offline"


@pytest.mark.asyncio
async def test_sweep_runner_heartbeat_orphans_emits_terminal_notice(db_factory_fixture):
    """sweep_runner_heartbeat_orphans emits terminal notice via outbox."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id, host_status="online")

    session_id = await _insert_session(db_factory_fixture, guild_id, host_id, "running")

    async with db_factory_fixture() as db:
        async with db.begin():
            await db.execute(
                text(
                    "UPDATE runner_hosts SET last_heartbeat_at ="
                    " now() - interval '30 hours' WHERE id = :hid"
                ),
                {"hid": host_id},
            )

    await sweep_runner_heartbeat_orphans(db_factory_fixture, orphan_recovery_ttl_hours=24)

    async with db_factory_fixture() as db:
        result = await db.execute(
            sa.select(RedisOutbox).where(
                sa.func.json_extract_path_text(RedisOutbox.payload, "session_id") == session_id
            )
        )
        outbox_row = result.scalar_one_or_none()
        assert outbox_row is not None
        assert outbox_row.envelope_type == "discord.terminal_notice.v1"
        assert outbox_row.payload["then_archive"] is True


@pytest.mark.asyncio
async def test_offline_host_recent_heartbeat_does_not_fail_sessions(db_factory_fixture):
    """A host offline but with a recent heartbeat (graceful restart) must NOT have
    its live sessions mass-failed."""
    guild_id, user_id, host_id = _unique_ids()
    # Host is already offline (e.g. marked by a graceful deploy restart) but its
    # heartbeat is recent.
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id, host_status="offline")

    session_id = await _insert_session(db_factory_fixture, guild_id, host_id, "running")

    # Heartbeat is fresh (just now) — well within the grace window.
    async with db_factory_fixture() as db:
        async with db.begin():
            await db.execute(
                text("UPDATE runner_hosts SET last_heartbeat_at = now() WHERE id = :hid"),
                {"hid": host_id},
            )

    count = await sweep_runner_heartbeat_orphans(db_factory_fixture, orphan_recovery_ttl_hours=24)
    assert count == 0

    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, uuid.UUID(session_id))
        assert row is not None
        assert row.status == "running"
        assert row.status_reason is None


@pytest.mark.asyncio
async def test_offline_host_stale_heartbeat_fails_sessions(db_factory_fixture):
    """A host offline AND with a stale heartbeat → its live sessions are failed."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id, host_status="offline")

    session_id = await _insert_session(db_factory_fixture, guild_id, host_id, "running")

    async with db_factory_fixture() as db:
        async with db.begin():
            await db.execute(
                text(
                    "UPDATE runner_hosts SET last_heartbeat_at ="
                    " now() - interval '30 hours' WHERE id = :hid"
                ),
                {"hid": host_id},
            )

    count = await sweep_runner_heartbeat_orphans(db_factory_fixture, orphan_recovery_ttl_hours=24)
    assert count >= 1

    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, uuid.UUID(session_id))
        assert row is not None
        assert row.status == "failed"
        assert row.status_reason == "runner_lost"
