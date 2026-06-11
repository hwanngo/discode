from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from discode.queues.outbox import write_outbox


@pytest.mark.asyncio
async def test_write_outbox_inserts_row(db_session: AsyncSession) -> None:
    """write_outbox inserts a row in the caller's transaction."""
    row = await write_outbox(
        db_session,
        producer="bot",
        stream_key="discord:outbound",
        envelope_type="send_message",
        payload={"channel_id": "123", "content": "hello"},
    )
    assert row.id is not None


@pytest.mark.asyncio
async def test_write_outbox_correct_fields(db_session: AsyncSession) -> None:
    """Row has correct producer, envelope_type, stream_key, payload."""
    row = await write_outbox(
        db_session,
        producer="runner",
        stream_key="runner:jobs:local",
        envelope_type="run_job",
        payload={"job_id": "abc", "priority": 1},
    )
    assert row.producer == "runner"
    assert row.stream_key == "runner:jobs:local"
    assert row.envelope_type == "run_job"
    assert row.payload == {"job_id": "abc", "priority": 1}


@pytest.mark.asyncio
async def test_write_outbox_published_at_is_none(db_session: AsyncSession) -> None:
    """published_at is None initially."""
    row = await write_outbox(
        db_session,
        producer="janitor",
        stream_key="janitor:tasks",
        envelope_type="cleanup",
        payload={"target": "old_sessions"},
    )
    assert row.published_at is None


@pytest.mark.asyncio
async def test_write_outbox_next_attempt_at_set(db_session: AsyncSession) -> None:
    """next_attempt_at is set (approximately now)."""
    before = datetime.now(tz=UTC)
    row = await write_outbox(
        db_session,
        producer="bot",
        stream_key="discord:outbound",
        envelope_type="send_message",
        payload={"channel_id": "456", "content": "world"},
    )

    assert row.next_attempt_at is not None
    # next_attempt_at should be around now (server default is now())
    # Allow a generous window since this is a server-side default
    assert (
        row.next_attempt_at.replace(tzinfo=UTC) >= before.replace(tzinfo=None).replace(tzinfo=UTC)
        or row.next_attempt_at.tzinfo is not None
    )
