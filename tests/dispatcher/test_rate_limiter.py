import time

import pytest

from discode.dispatcher.rate_limiter import ChannelRateLimiter


@pytest.mark.asyncio
async def test_first_n_calls_immediate(monkeypatch):
    # Bucket: capacity=5, refill 1 token / 1.0s
    rl = ChannelRateLimiter(capacity=5, refill_per_second=1.0)
    start = time.monotonic()
    for _ in range(5):
        await rl.acquire("ch1")
    assert time.monotonic() - start < 0.05  # all immediate


@pytest.mark.asyncio
async def test_sixth_call_waits_for_refill():
    rl = ChannelRateLimiter(capacity=5, refill_per_second=10.0)  # 100ms / token
    for _ in range(5):
        await rl.acquire("ch1")
    start = time.monotonic()
    await rl.acquire("ch1")
    elapsed = time.monotonic() - start
    assert 0.08 < elapsed < 0.25  # waited roughly one refill period


@pytest.mark.asyncio
async def test_independent_channels_dont_block_each_other():
    rl = ChannelRateLimiter(capacity=2, refill_per_second=0.5)
    for _ in range(2):
        await rl.acquire("ch1")
    start = time.monotonic()
    await rl.acquire("ch2")  # different channel — own bucket
    assert time.monotonic() - start < 0.05


@pytest.mark.asyncio
async def test_external_retry_after_overrides_bucket():
    rl = ChannelRateLimiter(capacity=5, refill_per_second=100.0)
    rl.note_retry_after("ch1", 0.2)
    start = time.monotonic()
    await rl.acquire("ch1")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.18
