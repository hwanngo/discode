from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

from discode.db.engine import make_engine, make_session_factory
from discode.db.models import (
    Event,
    Guild,
    IdempotencyKey,
    RedisOutbox,
    RunnerHost,
    User,
)
from discode.janitor.retention import (
    sweep_events,
    sweep_idempotency_keys,
    sweep_outbox,
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
async def test_sweep_idempotency_keys_deletes_expired(db_factory_fixture):
    """sweep_idempotency_keys deletes expired keys."""
    ikey = f"ikey-{uuid.uuid4().hex}"
    past = datetime.now(UTC) - timedelta(days=1)

    async with db_factory_fixture() as db:
        async with db.begin():
            db.add(
                IdempotencyKey(
                    envelope_type="test.v1",
                    idempotency_key=ikey,
                    expires_at=past,
                )
            )

    count = await sweep_idempotency_keys(db_factory_fixture)
    assert count >= 1

    async with db_factory_fixture() as db:
        result = await db.execute(
            text("SELECT 1 FROM idempotency_keys WHERE idempotency_key = :k"),
            {"k": ikey},
        )
        assert result.fetchone() is None


@pytest.mark.asyncio
async def test_sweep_events_deletes_old_events(db_factory_fixture):
    """sweep_events deletes old audit events."""
    async with db_factory_fixture() as db:
        async with db.begin():
            db.add(Event(type="test.event", payload={}))

    # Age the event
    async with db_factory_fixture() as db:
        async with db.begin():
            await db.execute(
                text(
                    "UPDATE events SET created_at = now() - interval '100 days'"
                    " WHERE type = 'test.event'"
                ),
            )

    count = await sweep_events(db_factory_fixture, retention_days=90)
    assert count >= 1


@pytest.mark.asyncio
async def test_sweep_outbox_deletes_old_published_rows(db_factory_fixture):
    """sweep_outbox deletes published rows older than the retention window,
    but keeps recent and unpublished rows."""
    producer = f"retention-{uuid.uuid4().hex[:8]}"

    async with db_factory_fixture() as db:
        async with db.begin():
            old_published = RedisOutbox(
                producer=producer,
                envelope_type="t.v1",
                stream_key="s",
                payload={},
                published_at=datetime.now(UTC),
            )
            recent_published = RedisOutbox(
                producer=producer,
                envelope_type="t.v1",
                stream_key="s",
                payload={},
                published_at=datetime.now(UTC),
            )
            unpublished = RedisOutbox(
                producer=producer,
                envelope_type="t.v1",
                stream_key="s",
                payload={},
            )
            db.add_all([old_published, recent_published, unpublished])
            await db.flush()
            old_id = old_published.id
            recent_id = recent_published.id
            unpublished_id = unpublished.id

    # Age the old published row's created_at past the retention window.
    async with db_factory_fixture() as db:
        async with db.begin():
            await db.execute(
                text(
                    "UPDATE redis_outbox SET created_at = now() - interval '30 days'"
                    " WHERE id = :id"
                ),
                {"id": old_id},
            )

    count = await sweep_outbox(db_factory_fixture, retention_days=7)
    assert count >= 1

    async with db_factory_fixture() as db:
        assert await db.get(RedisOutbox, old_id) is None
        assert await db.get(RedisOutbox, recent_id) is not None
        assert await db.get(RedisOutbox, unpublished_id) is not None


@pytest.mark.asyncio
async def test_sweep_outbox_deletes_old_exhausted_rows(db_factory_fixture):
    """sweep_outbox deletes attempt-exhausted (max_attempts) rows past the window."""
    producer = f"retention-{uuid.uuid4().hex[:8]}"

    async with db_factory_fixture() as db:
        async with db.begin():
            exhausted = RedisOutbox(
                producer=producer,
                envelope_type="t.v1",
                stream_key="s",
                payload={},
                error="max_attempts",
            )
            db.add(exhausted)
            await db.flush()
            exhausted_id = exhausted.id

    async with db_factory_fixture() as db:
        async with db.begin():
            await db.execute(
                text(
                    "UPDATE redis_outbox SET created_at = now() - interval '30 days'"
                    " WHERE id = :id"
                ),
                {"id": exhausted_id},
            )

    count = await sweep_outbox(db_factory_fixture, retention_days=7)
    assert count >= 1

    async with db_factory_fixture() as db:
        assert await db.get(RedisOutbox, exhausted_id) is None
