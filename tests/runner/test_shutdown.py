"""Runner graceful shutdown (audit 2026-10-12, finding #2)."""

from __future__ import annotations

import asyncio
import json
import signal

import pytest
import uvicorn

from discode.runner.main import _consume_input_stream, _ControlServer


class _Consumer:
    def __init__(self, batch):
        self.batch = batch
        self.acks: list[str] = []

    async def claim_pending(self):
        return []

    async def read_new(self, **kwargs):
        batch, self.batch = self.batch, []
        if not batch:
            await asyncio.sleep(0.01)
        return batch

    async def ack(self, mid):
        self.acks.append(mid)


def _msg(mid, sid):
    return mid, {"payload": json.dumps({"session_id": sid, "input": mid})}


@pytest.mark.asyncio
async def test_stop_drains_running_turn_and_leaves_queued_pending():
    started = asyncio.Event()
    release = asyncio.Event()
    ran: list[str] = []

    async def handler(fields):
        mid = json.loads(fields["payload"])["input"]
        ran.append(mid)
        if mid == "a1":
            started.set()
            await release.wait()
        return True

    consumer = _Consumer([_msg("a1", "a"), _msg("a2", "a")])
    stop = asyncio.Event()
    task = asyncio.create_task(
        _consume_input_stream(consumer, handler, stop=stop, drain_timeout_seconds=5)
    )
    await asyncio.wait_for(started.wait(), 1)
    stop.set()
    await asyncio.sleep(0.05)
    assert not task.done()  # still draining a1
    release.set()
    await asyncio.wait_for(task, 1)

    assert ran == ["a1"]  # a2 never started
    assert consumer.acks == ["a1"]  # a2 stays pending for redelivery


@pytest.mark.asyncio
async def test_drain_timeout_cancels_stuck_turn():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(fields):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return True

    consumer = _Consumer([_msg("a1", "a")])
    stop = asyncio.Event()
    task = asyncio.create_task(
        _consume_input_stream(consumer, handler, stop=stop, drain_timeout_seconds=0.05)
    )
    await asyncio.wait_for(started.wait(), 1)
    stop.set()
    await asyncio.wait_for(task, 1)
    assert cancelled.is_set()
    assert consumer.acks == []


@pytest.mark.asyncio
async def test_control_server_does_not_capture_signals():
    sentinel = signal.getsignal(signal.SIGTERM)
    server = _ControlServer(uvicorn.Config(app=lambda *a: None))
    with server.capture_signals():
        assert signal.getsignal(signal.SIGTERM) is sentinel
    assert signal.getsignal(signal.SIGTERM) is sentinel
