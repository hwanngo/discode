from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from discode.bot.typing_indicator import TypingIndicatorTask

logger = logging.getLogger(__name__)

_DEFAULT_REFRESH_INTERVAL = 8.0
_DEFAULT_MAX_DURATION = 360.0


TriggerFactory = Callable[[str], Callable[[], Awaitable[None]]]


class TypingTracker:
    """Per-bot-process typing-indicator state machine.

    Maintains a per-thread pending user-message counter. The indicator is
    visible iff the counter is > 0; the first user message starts it, every
    bot reply decrements, hitting zero stops it. A safety timeout zeroes the
    counter so a lost reply or runner crash never leaves the indicator stuck.
    """

    def __init__(
        self,
        *,
        trigger_factory: TriggerFactory,
        refresh_interval: float = _DEFAULT_REFRESH_INTERVAL,
        max_duration: float = _DEFAULT_MAX_DURATION,
    ) -> None:
        self._trigger_factory = trigger_factory
        self._refresh = refresh_interval
        self._max = max_duration
        self._counts: dict[str, int] = {}
        self._tasks: dict[str, TypingIndicatorTask] = {}
        self._timeouts: dict[str, asyncio.Task[None]] = {}

    def pending(self, thread_id: str) -> int:
        return self._counts.get(thread_id, 0)

    def is_running(self, thread_id: str) -> bool:
        return thread_id in self._tasks

    async def note_user_message(self, thread_id: str) -> None:
        self._counts[thread_id] = self._counts.get(thread_id, 0) + 1
        if thread_id not in self._tasks:
            task = TypingIndicatorTask(
                trigger=self._trigger_factory(thread_id),
                refresh_interval=self._refresh,
                max_duration=self._max,
            )
            self._tasks[thread_id] = task
            await task.start()
            self._timeouts[thread_id] = asyncio.create_task(
                self._safety_timeout(thread_id),
                name=f"typing-safety-{thread_id}",
            )
            logger.info(
                "typing_tracker: start sid_thread=%s pending=%d",
                thread_id,
                self._counts[thread_id],
            )

    async def note_bot_message(self, thread_id: str) -> None:
        current = self._counts.get(thread_id, 0)
        if current <= 0:
            return
        self._counts[thread_id] = current - 1
        if self._counts[thread_id] == 0:
            await self._stop(thread_id, reason="zero")

    async def _safety_timeout(self, thread_id: str) -> None:
        try:
            await asyncio.sleep(self._max)
        except asyncio.CancelledError:
            return
        if thread_id in self._tasks:
            await self._stop(thread_id, reason="safety_timeout")

    async def _stop(self, thread_id: str, *, reason: str) -> None:
        task = self._tasks.pop(thread_id, None)
        self._counts[thread_id] = 0
        timeout_task = self._timeouts.pop(thread_id, None)
        if timeout_task is not None and not timeout_task.done():
            timeout_task.cancel()
            try:
                await timeout_task
            except asyncio.CancelledError, Exception:
                pass
        if task is not None:
            await task.stop()
        logger.info("typing_tracker: stop sid_thread=%s reason=%s", thread_id, reason)

    async def shutdown(self) -> None:
        for thread_id in list(self._tasks):
            await self._stop(thread_id, reason="shutdown")
