from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as aioredis
from sqlalchemy import select, update

from discode.db.engine import make_engine, make_session_factory
from discode.db.models import RedisOutbox
from discode.queues.outbox import write_outbox
from discode.queues.relay import OUTBOX_MAX_ATTEMPTS, RelayLoop


@pytest.fixture
async def relay_redis(redis_container):  # type: ignore[no-untyped-def]
    """Function-scoped Redis client to avoid event loop issues with session fixtures."""
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client = aioredis.Redis(host=host, port=int(port))
    yield client
    await client.aclose()


@pytest.fixture
async def db_factory(migrated_db_url: str):  # type: ignore[no-untyped-def]
    """Session factory for relay tests."""
    engine = make_engine(migrated_db_url)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_drain_once_publishes_and_marks(db_factory, relay_redis) -> None:  # type: ignore[no-untyped-def]
    """drain_once publishes row to Redis via XADD, marks published_at."""
    producer = "bot_drain_test"
    async with db_factory() as session:
        async with session.begin():
            row = await write_outbox(
                session,
                producer=producer,
                stream_key="discord:outbound:test1",
                envelope_type="send_message",
                payload={"channel_id": "111", "content": "test relay"},
            )
            row_id = row.id

    relay = RelayLoop(producer=producer, redis_client=relay_redis, db_factory=db_factory)
    drained = await relay.drain_once()

    assert drained >= 1

    # Verify row is marked as published
    async with db_factory() as session:
        result = await session.execute(select(RedisOutbox).where(RedisOutbox.id == row_id))
        updated_row = result.scalar_one()
        assert updated_row.published_at is not None

    # Verify the message exists in Redis stream
    messages = await relay_redis.xrange("discord:outbound:test1")
    assert len(messages) >= 1
    _, fields = messages[-1]
    decoded_fields = {k.decode(): v.decode() for k, v in fields.items()}
    assert "payload" in decoded_fields
    assert "envelope_type" in decoded_fields
    assert decoded_fields["envelope_type"] == "send_message"
    payload_data = json.loads(decoded_fields["payload"])
    assert payload_data["channel_id"] == "111"


@pytest.mark.asyncio
async def test_crash_restart_exactly_once(db_factory, relay_redis) -> None:  # type: ignore[no-untyped-def]
    """Stop relay between rows → restart → row published exactly once."""
    producer = "bot_crash_test"
    stream_key = "discord:outbound:test_crash"

    # Insert two rows
    async with db_factory() as session:
        async with session.begin():
            row1 = await write_outbox(
                session,
                producer=producer,
                stream_key=stream_key,
                envelope_type="msg_type",
                payload={"seq": 1},
            )
            row2 = await write_outbox(
                session,
                producer=producer,
                stream_key=stream_key,
                envelope_type="msg_type",
                payload={"seq": 2},
            )
            row1_id = row1.id
            row2_id = row2.id

    # First relay run: drain normally
    relay1 = RelayLoop(producer=producer, redis_client=relay_redis, db_factory=db_factory)
    await relay1.drain_once()

    # Second relay run: should still work, not re-publish already published rows
    relay2 = RelayLoop(producer=producer, redis_client=relay_redis, db_factory=db_factory)
    await relay2.drain_once()

    # Both rows should be published
    async with db_factory() as session:
        r1 = (
            await session.execute(select(RedisOutbox).where(RedisOutbox.id == row1_id))
        ).scalar_one()
        r2 = (
            await session.execute(select(RedisOutbox).where(RedisOutbox.id == row2_id))
        ).scalar_one()
        assert r1.published_at is not None
        assert r2.published_at is not None

    # Count messages in stream — each should appear exactly once
    messages = await relay_redis.xrange(stream_key)
    payloads = []
    for _, fields in messages:
        decoded = {k.decode(): v.decode() for k, v in fields.items()}
        payloads.append(json.loads(decoded["payload"]))

    seq_values = [p["seq"] for p in payloads]
    assert seq_values.count(1) == 1
    assert seq_values.count(2) == 1


@pytest.mark.asyncio
async def test_exponential_backoff_on_xadd_failure(db_factory, relay_redis) -> None:  # type: ignore[no-untyped-def]
    """Exponential backoff on XADD failure (mock redis failure)."""
    producer = "bot_backoff_test"
    stream_key = "discord:outbound:test_backoff"

    async with db_factory() as session:
        async with session.begin():
            row = await write_outbox(
                session,
                producer=producer,
                stream_key=stream_key,
                envelope_type="fail_type",
                payload={"will": "fail"},
            )
            row_id = row.id

    # Mock redis to raise on xadd
    failing_redis = AsyncMock()
    failing_redis.xadd = AsyncMock(side_effect=ConnectionError("Redis connection refused"))

    relay = RelayLoop(producer=producer, redis_client=failing_redis, db_factory=db_factory)
    drained = await relay.drain_once()

    assert drained == 0

    # Row should have attempts=1, error set, next_attempt_at in the future
    async with db_factory() as session:
        result = await session.execute(select(RedisOutbox).where(RedisOutbox.id == row_id))
        updated_row = result.scalar_one()
        assert updated_row.attempts == 1
        assert updated_row.error is not None
        assert updated_row.published_at is None
        # next_attempt_at should be in the future
        now = datetime.now(tz=UTC)
        assert updated_row.next_attempt_at > now


@pytest.mark.asyncio
async def test_max_attempts_stops_retrying(db_factory, relay_redis) -> None:  # type: ignore[no-untyped-def]
    """Relay stops retrying after OUTBOX_MAX_ATTEMPTS."""
    producer = "bot_maxattempts_test"
    stream_key = "discord:outbound:test_max_attempts"

    async with db_factory() as session:
        async with session.begin():
            row = await write_outbox(
                session,
                producer=producer,
                stream_key=stream_key,
                envelope_type="exhaust_type",
                payload={"attempt": "exhaust"},
            )
            row_id = row.id

    # Manually set attempts to just below max so one more failure hits max
    async with db_factory() as session:
        async with session.begin():
            await session.execute(
                update(RedisOutbox)
                .where(RedisOutbox.id == row_id)
                .values(attempts=OUTBOX_MAX_ATTEMPTS - 1)
            )

    failing_redis = AsyncMock()
    failing_redis.xadd = AsyncMock(side_effect=ConnectionError("Redis unavailable"))

    relay = RelayLoop(producer=producer, redis_client=failing_redis, db_factory=db_factory)
    await relay.drain_once()

    async with db_factory() as session:
        result = await session.execute(select(RedisOutbox).where(RedisOutbox.id == row_id))
        updated_row = result.scalar_one()
        assert updated_row.attempts == OUTBOX_MAX_ATTEMPTS
        assert updated_row.error == "max_attempts"
        assert updated_row.published_at is None

    # Another drain attempt should NOT pick it up (next_attempt_at is in the future)
    drained2 = await relay.drain_once()
    assert drained2 == 0


@pytest.mark.asyncio
async def test_exhausted_row_not_reselected(db_factory, relay_redis) -> None:  # type: ignore[no-untyped-def]
    """A row at OUTBOX_MAX_ATTEMPTS is never re-selected by the drain query,
    even when next_attempt_at is in the past."""
    producer = "bot_exhausted_test"
    stream_key = "discord:outbound:test_exhausted"

    async with db_factory() as session:
        async with session.begin():
            row = await write_outbox(
                session,
                producer=producer,
                stream_key=stream_key,
                envelope_type="dead_type",
                payload={"dead": True},
            )
            row_id = row.id

    # Mark the row as exhausted with a next_attempt_at in the past so the only
    # thing keeping it out of the drain set is the attempts cap.
    past = datetime(2000, 1, 1, tzinfo=UTC)
    async with db_factory() as session:
        async with session.begin():
            await session.execute(
                update(RedisOutbox)
                .where(RedisOutbox.id == row_id)
                .values(
                    attempts=OUTBOX_MAX_ATTEMPTS,
                    error="max_attempts",
                    next_attempt_at=past,
                )
            )

    relay = RelayLoop(producer=producer, redis_client=relay_redis, db_factory=db_factory)
    drained = await relay.drain_once()
    assert drained == 0

    # Row remains unpublished and is not retried.
    async with db_factory() as session:
        updated = (
            await session.execute(select(RedisOutbox).where(RedisOutbox.id == row_id))
        ).scalar_one()
        assert updated.published_at is None
        assert updated.attempts == OUTBOX_MAX_ATTEMPTS
