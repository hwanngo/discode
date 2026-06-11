from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class TypingIndicatorTask:
    """Background task that calls `trigger()` immediately and then every
    `refresh_interval` seconds, up to `max_duration`. Discord's typing
    indicator auto-clears after ~10s, so callers should set `refresh_interval`
    below 10s to keep it visible.
    """

    def __init__(
        self,
        *,
        trigger: Callable[[], Awaitable[None]],
        refresh_interval: float = 8.0,
        max_duration: float = 300.0,
    ) -> None:
        self._trigger = trigger
        self._refresh = refresh_interval
        self._max = max_duration
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="typing-indicator")

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._max
        while loop.time() < deadline and not self._stop.is_set():
            try:
                await self._trigger()
            except Exception:
                logger.exception("typing trigger failed (non-fatal)")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._refresh)
                return  # stop() called
            except TimeoutError:
                continue

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
