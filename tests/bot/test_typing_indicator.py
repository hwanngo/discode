import asyncio

import pytest

from discode.bot.typing_indicator import TypingIndicatorTask


@pytest.mark.asyncio
async def test_refreshes_until_stopped():
    calls: list[float] = []

    async def fake_trigger():
        calls.append(asyncio.get_event_loop().time())

    task = TypingIndicatorTask(
        trigger=fake_trigger,
        refresh_interval=0.05,
        max_duration=0.5,
    )
    await task.start()
    await asyncio.sleep(0.16)
    await task.stop()
    # Should have fired at t=0, ~0.05, ~0.10 (CI-tolerant bounds)
    assert 2 <= len(calls) <= 5


@pytest.mark.asyncio
async def test_max_duration_bounds_lifetime():
    calls: list[None] = []

    async def fake_trigger():
        calls.append(None)

    task = TypingIndicatorTask(
        trigger=fake_trigger,
        refresh_interval=0.05,
        max_duration=0.12,
    )
    await task.start()
    await asyncio.sleep(0.30)
    # Auto-stopped at 0.12s — only ~3 calls (t=0, 0.05, 0.10), CI-tolerant
    assert 1 <= len(calls) <= 5
    assert task._task.done()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_stop_is_idempotent():
    async def fake_trigger():
        pass

    task = TypingIndicatorTask(trigger=fake_trigger, refresh_interval=0.05, max_duration=1.0)
    await task.start()
    await task.stop()
    await task.stop()  # must not raise
