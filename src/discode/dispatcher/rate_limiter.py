from __future__ import annotations

import asyncio
import time


class ChannelRateLimiter:
    """Per-channel token bucket. Discord's per-channel limit is ~5 msg / 5s;
    defaults match that so tight loops self-throttle.

    `note_retry_after` lets the caller feed an authoritative server-side
    cool-off (from a 429 Retry-After header) — subsequent acquires for that
    channel sleep until the cool-off has elapsed.
    """

    def __init__(self, capacity: int = 5, refill_per_second: float = 1.0) -> None:
        self._capacity = capacity
        self._refill = refill_per_second
        self._tokens: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._cooloff_until: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, channel_id: str) -> asyncio.Lock:
        lock = self._locks.get(channel_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[channel_id] = lock
        return lock

    def note_retry_after(self, channel_id: str, seconds: float) -> None:
        self._cooloff_until[channel_id] = time.monotonic() + max(0.0, seconds)

    async def acquire(self, channel_id: str) -> None:
        async with self._lock(channel_id):
            now = time.monotonic()
            cooloff = self._cooloff_until.get(channel_id, 0.0)
            if cooloff > now:
                await asyncio.sleep(cooloff - now)
                now = time.monotonic()
            tokens = self._tokens.get(channel_id, float(self._capacity))
            last = self._last.get(channel_id, now)
            tokens = min(self._capacity, tokens + (now - last) * self._refill)
            if tokens < 1.0:
                wait = (1.0 - tokens) / self._refill
                await asyncio.sleep(wait)
                tokens = 1.0
                now = time.monotonic()
            self._tokens[channel_id] = tokens - 1.0
            self._last[channel_id] = now
