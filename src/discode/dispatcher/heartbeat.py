from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 15


class HeartbeatTask:
    """Posts/patches a 15s heartbeat embed for active sessions."""

    def __init__(
        self, session_id: str, thread_id: str, tool_name: str, send_heartbeat: Any
    ) -> None:
        self.session_id = session_id
        self.thread_id = thread_id
        self.tool_name = tool_name
        self._send_heartbeat = send_heartbeat  # callable
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                try:
                    await self._send_heartbeat(self.session_id, self.tool_name)
                except Exception:
                    logger.exception("heartbeat error for session %s", self.session_id)
        except asyncio.CancelledError:
            raise
