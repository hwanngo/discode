from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from discode.runner.main import _consume_runner_stream, dispatch_runner_job


@pytest.mark.asyncio
async def test_dispatch_runner_job_routes_create(monkeypatch: pytest.MonkeyPatch) -> None:
    create_mock = AsyncMock()
    monkeypatch.setattr("discode.runner.main.run_create_job", create_mock)
    monkeypatch.setattr("discode.runner.main.parse_create_job_payload", lambda payload: payload)

    handled = await dispatch_runner_job(
        {
            "envelope_type": "runner.create_session.v1",
            "payload": json.dumps({"session_id": "s1"}),
        },
        host_id="host-1",
        db_factory=AsyncMock(),
        session_store={},
    )

    assert handled is True
    create_mock.assert_awaited_once()


def _input_fields() -> dict[str, str]:
    return {
        "envelope_type": "runner.send_input.v1",
        "payload": json.dumps(
            {
                "session_id": "s1",
                "guild_id": "g1",
                "thread_id": "t1",
                "idempotency_key": "ikey-1",
                "text": "hello",
            }
        ),
    }


@pytest.mark.asyncio
async def test_dispatch_runner_job_routes_input(monkeypatch: pytest.MonkeyPatch) -> None:
    input_mock = AsyncMock(return_value="delivered")
    monkeypatch.setattr("discode.runner.main.run_input_job", input_mock)

    handled = await dispatch_runner_job(
        _input_fields(),
        host_id="host-1",
        db_factory=AsyncMock(),
        session_store={},
    )

    assert handled is True
    input_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_runner_job_input_deferred_not_acked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deferred input must NOT be acked (leave pending for redelivery)."""
    monkeypatch.setattr("discode.runner.main.run_input_job", AsyncMock(return_value="deferred"))

    handled = await dispatch_runner_job(
        _input_fields(),
        host_id="host-1",
        db_factory=AsyncMock(),
        session_store={},
    )

    assert handled is False


@pytest.mark.asyncio
async def test_dispatch_runner_job_input_aborted_is_acked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An aborted input is a terminal decision — ack it (do not redeliver)."""
    monkeypatch.setattr("discode.runner.main.run_input_job", AsyncMock(return_value="aborted"))

    handled = await dispatch_runner_job(
        _input_fields(),
        host_id="host-1",
        db_factory=AsyncMock(),
        session_store={},
    )

    assert handled is True


@pytest.mark.asyncio
async def test_dispatch_runner_job_input_skipped_replay_is_acked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "discode.runner.main.run_input_job", AsyncMock(return_value="skipped_replay")
    )

    handled = await dispatch_runner_job(
        _input_fields(),
        host_id="host-1",
        db_factory=AsyncMock(),
        session_store={},
    )

    assert handled is True


@pytest.mark.asyncio
async def test_dispatch_runner_job_routes_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop handler removes session from session_store and returns True."""
    session_store = {"s1": {"tool": "claude"}}

    db = AsyncMock()
    result = MagicMock()
    result.first.return_value = (1,)
    db.execute.return_value = result
    db.begin = MagicMock(return_value=db)
    factory = MagicMock(return_value=db)
    db.__aenter__.return_value = db
    stop_handled = await dispatch_runner_job(
        {
            "envelope_type": "runner.stop_session.v1",
            "payload": json.dumps(
                {"session_id": "s1", "guild_id": "g1", "thread_id": "t1", "lifecycle_version": 1}
            ),
        },
        host_id="host-1",
        db_factory=factory,
        session_store=session_store,
    )

    assert stop_handled is True
    assert "s1" not in session_store


@pytest.mark.asyncio
async def test_dispatch_runner_job_routes_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    resume_mock = AsyncMock()
    monkeypatch.setattr("discode.runner.main.run_resume_job", resume_mock)

    resume_handled = await dispatch_runner_job(
        {
            "envelope_type": "runner.resume_session.v1",
            "payload": json.dumps(
                {"session_id": "s1", "guild_id": "g1", "thread_id": "t1", "lifecycle_version": 1}
            ),
        },
        host_id="host-1",
        db_factory=AsyncMock(),
        session_store={},
    )

    assert resume_handled is True
    resume_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_consumer_acknowledges_processed_messages() -> None:
    consumer = AsyncMock()
    consumer.claim_pending = AsyncMock(
        side_effect=[[("1-0", {"payload": "{}"})], asyncio.CancelledError]
    )
    consumer.ack = AsyncMock()

    async def handler(fields: dict[str, str]) -> bool:
        assert fields == {"payload": "{}"}
        return True

    with pytest.raises(asyncio.CancelledError):
        await _consume_runner_stream(consumer, handler)

    consumer.ack.assert_awaited_once_with("1-0")


@pytest.mark.asyncio
async def test_runner_stream_survives_redis_read_error() -> None:
    """A transient Redis error retries instead of killing the consumer task."""
    consumer = AsyncMock()
    consumer.claim_pending = AsyncMock(
        side_effect=[
            ConnectionError("redis down"),
            [("1-0", {"payload": "{}"})],
            asyncio.CancelledError,
        ]
    )
    consumer.ack = AsyncMock()

    with (
        patch("discode.runner.main._STREAM_RETRY_SECONDS", 0),
        pytest.raises(asyncio.CancelledError),
    ):
        await _consume_runner_stream(consumer, AsyncMock(return_value=True))

    consumer.ack.assert_awaited_once_with("1-0")


@pytest.mark.asyncio
async def test_runner_stream_dead_letters_after_budget() -> None:
    consumer = AsyncMock()
    consumer.claim_pending = AsyncMock(
        side_effect=[[("1-0", {"payload": "{}"})], asyncio.CancelledError]
    )
    consumer.delivery_count = AsyncMock(return_value=3)
    consumer.dead_letter_key = "discode:deadletter:runner:jobs:h"

    with pytest.raises(asyncio.CancelledError):
        await _consume_runner_stream(
            consumer, AsyncMock(side_effect=RuntimeError("boom")), max_deliveries=3
        )

    consumer.dead_letter.assert_awaited_once_with(
        "1-0", {"payload": "{}"}, reason="handler_exception", attempts=3
    )
    consumer.ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_runner_stream_survives_ack_failure() -> None:
    consumer = AsyncMock()
    consumer.claim_pending = AsyncMock(
        side_effect=[[("1-0", {}), ("2-0", {})], asyncio.CancelledError]
    )
    consumer.ack = AsyncMock(side_effect=[ConnectionError("redis down"), None])

    with pytest.raises(asyncio.CancelledError):
        await _consume_runner_stream(consumer, AsyncMock(return_value=True))

    assert consumer.ack.await_count == 2
