import asyncio
import json

import pytest

from discode.runner.main import _consume_input_stream


@pytest.mark.asyncio
async def test_other_session_progresses_while_same_session_preserves_order():
    released = asyncio.Event()
    other_finished = asyncio.Event()
    all_finished = asyncio.Event()
    order = []

    class Consumer:
        sent = False
        acknowledgements = []

        async def claim_pending(self):
            return []

        async def read_new(self, **kwargs):
            if self.sent:
                await asyncio.sleep(0.01)
                return []
            self.sent = True
            return [
                (mid, {"payload": json.dumps({"session_id": sid, "input": mid})})
                for mid, sid in [("a1", "a"), ("a2", "a"), ("b1", "b")]
            ]

        async def ack(self, mid):
            self.acknowledgements.append(mid)
            if len(self.acknowledgements) == 3:
                all_finished.set()

    async def handler(fields):
        mid = json.loads(fields["payload"])["input"]
        if mid == "a1":
            await released.wait()
        order.append(mid)
        if mid == "b1":
            other_finished.set()
        return True

    consumer = Consumer()
    task = asyncio.create_task(_consume_input_stream(consumer, handler, concurrency=2))
    try:
        await asyncio.wait_for(other_finished.wait(), 1)
        assert order == ["b1"]
        released.set()
        await asyncio.wait_for(all_finished.wait(), 1)
        assert order == ["b1", "a1", "a2"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
