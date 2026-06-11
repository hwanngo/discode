from __future__ import annotations

import asyncio

import pytest

from discode.queues.consumers.runner_cg import RUNNER_JOB_MAX_DELIVERIES
from discode.runner.main import _consume_runner_stream


class FakeConsumer:
    """A consumer that always redelivers the same pending message until acked.

    `delivery_count` returns 0 (as a real Redis error would) so the loop must
    fail closed using its own local attempt tracking rather than relying on the
    server-side delivery count.
    """

    def __init__(self, message_id: str = "1-0", fields: dict[str, str] | None = None) -> None:
        self.message_id = message_id
        self.fields = fields if fields is not None else {"payload": "{}"}
        self.acked: list[str] = []
        self.deliveries = 0

    async def claim_pending(self):
        if self.message_id in self.acked:
            # Nothing pending anymore — let read_new spin so the loop yields.
            raise asyncio.CancelledError
        self.deliveries += 1
        if self.deliveries > 1000:
            raise asyncio.CancelledError  # safety valve against infinite loop
        return [(self.message_id, self.fields)]

    async def read_new(self, count: int = 10, block_ms: int = 250):
        return []

    async def ack(self, message_id: str) -> None:
        self.acked.append(message_id)

    async def delivery_count(self, message_id: str) -> int:
        return 0  # simulate Redis error / fail-open server count


@pytest.mark.asyncio
async def test_poison_message_dropped_after_budget() -> None:
    """A handler that always raises must stop being retried after the budget."""
    consumer = FakeConsumer()

    async def handler(fields: dict[str, str]) -> bool:
        raise ValueError("boom")

    with pytest.raises(asyncio.CancelledError):
        await _consume_runner_stream(consumer, handler)

    # Must be acked (dropped) exactly once, and not retried forever.
    assert consumer.acked == ["1-0"]
    assert consumer.deliveries <= RUNNER_JOB_MAX_DELIVERIES + 1


@pytest.mark.asyncio
async def test_unparseable_payload_does_not_wedge_loop() -> None:
    """A payload that fails json.loads inside the handler is dropped, not looped."""
    consumer = FakeConsumer(fields={"payload": "{not json"})

    async def handler(fields: dict[str, str]) -> bool:
        import json

        json.loads(fields["payload"])  # raises
        return True

    with pytest.raises(asyncio.CancelledError):
        await _consume_runner_stream(consumer, handler)

    assert consumer.acked == ["1-0"]


@pytest.mark.asyncio
async def test_transient_failure_recovers_on_retry() -> None:
    """A failure that succeeds on attempt 2 is acked normally, never dropped."""
    consumer = FakeConsumer()
    attempts = {"n": 0}

    async def handler(fields: dict[str, str]) -> bool:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient")
        return True

    with pytest.raises(asyncio.CancelledError):
        await _consume_runner_stream(consumer, handler)

    # Acked after the successful 2nd attempt — exactly once, success path.
    assert consumer.acked == ["1-0"]
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_handler_returning_false_redelivers_then_drops() -> None:
    """A handler that returns False (e.g. permanent defer) is dropped after budget."""
    consumer = FakeConsumer()

    async def handler(fields: dict[str, str]) -> bool:
        return False

    with pytest.raises(asyncio.CancelledError):
        await _consume_runner_stream(consumer, handler)

    assert consumer.acked == ["1-0"]
    assert consumer.deliveries <= RUNNER_JOB_MAX_DELIVERIES + 1
