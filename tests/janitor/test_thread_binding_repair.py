"""Tests for the janitor sweep_session_thread_binding_drift function.

TDD: these tests were written before the implementation.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from discode.db.engine import make_engine, make_session_factory
from discode.db.models import Guild, RedisOutbox, RunnerHost, User
from discode.db.models import Session as SessionModel
from discode.janitor.lifecycle import sweep_session_thread_binding_drift


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


async def _insert_session(
    db_factory,
    guild_id,
    host_id,
    *,
    status: str = "creating",
    thread_id: str | None = None,
    status_reason: str | None = None,
    repair_attempts: int = 0,
) -> str:
    sid = uuid.uuid4()
    async with db_factory() as db:
        async with db.begin():
            await db.execute(
                text("""
                    INSERT INTO sessions (
                        id, guild_id, tool, name, cwd, cwd_realpath,
                        hook_secret_hash, status,
                        host_id, thread_id, status_reason, repair_attempts
                    ) VALUES (
                        :id, :guild_id, 'claude', 'test', '/tmp', '/tmp',
                        'hash', :status,
                        :host_id, :thread_id, :status_reason, :repair_attempts
                    )
                """),
                {
                    "id": str(sid),
                    "guild_id": guild_id,
                    "status": status,
                    "host_id": host_id,
                    "thread_id": thread_id,
                    "status_reason": status_reason,
                    "repair_attempts": repair_attempts,
                },
            )
    return str(sid)


async def _insert_outbox_create_session(db_factory, session_id: str, thread_id: str) -> int:
    """Insert a runner.create_session.v1 outbox row with thread_id in payload."""
    async with db_factory() as db:
        async with db.begin():
            row = RedisOutbox(
                producer="bot",
                envelope_type="runner.create_session.v1",
                stream_key="runner:commands",
                payload={"session_id": session_id, "thread_id": thread_id},
            )
            db.add(row)
            await db.flush()
            return row.id


# ---------------------------------------------------------------------------
# Test 1: Session with NULL thread_id but outbox has thread_id → repaired
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repair_missing_thread_id_updates_session_and_outbox(db_factory_fixture):
    """A 'creating' session with thread_id=NULL is repaired when outbox payload has a thread_id."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    thread_id = f"thread-{uuid.uuid4().hex[:8]}"
    session_id = await _insert_session(
        db_factory_fixture,
        guild_id,
        host_id,
        status="creating",
        thread_id=None,
        status_reason=None,
    )

    # Outbox already has the thread_id (set before the session row was updated)
    await _insert_outbox_create_session(db_factory_fixture, session_id, thread_id)

    repaired, dead_lettered = await sweep_session_thread_binding_drift(db_factory_fixture)

    assert repaired >= 1

    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, uuid.UUID(session_id))
        assert row is not None
        assert row.thread_id == thread_id
        # status_reason should be cleared on success
        assert row.status_reason not in ("thread_bind_failed", "thread_bind_repair_exhausted")


# ---------------------------------------------------------------------------
# Test 2: Session with status_reason='thread_bind_failed' is repaired from outbox
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repair_status_reason_session_is_repaired(db_factory_fixture):
    """A session with status_reason='thread_bind_failed' is repaired when outbox has thread_id."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    thread_id = f"thread-{uuid.uuid4().hex[:8]}"
    session_id = await _insert_session(
        db_factory_fixture,
        guild_id,
        host_id,
        status="creating",
        thread_id=None,
        status_reason="thread_bind_failed",
    )

    await _insert_outbox_create_session(db_factory_fixture, session_id, thread_id)

    repaired, dead_lettered = await sweep_session_thread_binding_drift(db_factory_fixture)

    assert repaired >= 1

    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, uuid.UUID(session_id))
        assert row is not None
        assert row.thread_id == thread_id
        assert row.status_reason != "thread_bind_failed"
        assert row.status_reason != "thread_bind_repair_exhausted"


# ---------------------------------------------------------------------------
# Test 3: After max_attempts exhausted → dead-letter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_letter_after_max_attempts(db_factory_fixture):
    """A session that has been attempted max_attempts times with no outbox is dead-lettered."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    session_id = await _insert_session(
        db_factory_fixture,
        guild_id,
        host_id,
        status="creating",
        thread_id=None,
        status_reason="thread_bind_failed",
        repair_attempts=3,  # already at max_attempts=3
    )
    # No outbox row inserted → repair is not possible

    repaired, dead_lettered = await sweep_session_thread_binding_drift(
        db_factory_fixture, max_attempts=3
    )

    assert dead_lettered >= 1

    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, uuid.UUID(session_id))
        assert row is not None
        assert row.status_reason == "thread_bind_repair_exhausted"
        assert row.thread_id is None  # still unbound — admin must intervene


# ---------------------------------------------------------------------------
# Test 4: Session with no outbox payload is dead-lettered after one sweep pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_with_no_outbox_payload_dead_letters(db_factory_fixture):
    """A session with no outbox entry hits max attempts and gets dead-lettered."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    session_id = await _insert_session(
        db_factory_fixture,
        guild_id,
        host_id,
        status="creating",
        thread_id=None,
        status_reason=None,
        repair_attempts=3,  # exhausted
    )
    # No outbox row

    repaired, dead_lettered = await sweep_session_thread_binding_drift(
        db_factory_fixture, max_attempts=3
    )

    assert dead_lettered >= 1

    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, uuid.UUID(session_id))
        assert row is not None
        assert row.status_reason == "thread_bind_repair_exhausted"


# ---------------------------------------------------------------------------
# Test 5: Idempotency — running sweep twice doesn't double-repair
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_is_idempotent(db_factory_fixture):
    """Running the sweep twice on an already-repaired session does nothing on second run."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    thread_id = f"thread-{uuid.uuid4().hex[:8]}"
    session_id = await _insert_session(
        db_factory_fixture,
        guild_id,
        host_id,
        status="creating",
        thread_id=None,
        status_reason="thread_bind_failed",
    )
    await _insert_outbox_create_session(db_factory_fixture, session_id, thread_id)

    # First sweep — repairs
    r1, d1 = await sweep_session_thread_binding_drift(db_factory_fixture)
    assert r1 >= 1

    # Second sweep — session is already bound (thread_id not NULL), not picked up again
    r2, d2 = await sweep_session_thread_binding_drift(db_factory_fixture)
    # The already-repaired session should not appear in drifted set again
    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, uuid.UUID(session_id))
        assert row.thread_id == thread_id
        # repair_attempts should not keep incrementing after success
        assert row.repair_attempts <= 1


# ---------------------------------------------------------------------------
# Test 6: Already-bound session (thread_id set) is NOT touched by sweep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_already_bound_session_not_touched(db_factory_fixture):
    """Sessions with a non-null thread_id are not considered drifted."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    thread_id = f"thread-{uuid.uuid4().hex[:8]}"
    session_id = await _insert_session(
        db_factory_fixture,
        guild_id,
        host_id,
        status="creating",
        thread_id=thread_id,  # already bound
        status_reason=None,
    )

    repaired, dead_lettered = await sweep_session_thread_binding_drift(db_factory_fixture)

    async with db_factory_fixture() as db:
        row = await db.get(SessionModel, uuid.UUID(session_id))
        assert row is not None
        assert row.thread_id == thread_id  # unchanged
        assert row.repair_attempts == 0  # untouched
