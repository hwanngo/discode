from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.dispatcher.handlers.terminal_notice import _claim_act_finalize

logger = logging.getLogger(__name__)

_LIVE_STATUSES = frozenset({"creating", "resuming", "running", "idle"})


async def handle_archive_thread(
    envelope: dict[str, Any],
    *,
    db_factory: async_sessionmaker[AsyncSession],
    archive_discord_thread: Callable[..., Awaitable[None]],
) -> bool:
    """Archive once per delivery key and record completion only after success."""
    session_id = envelope.get("session_id", "")
    thread_id = envelope.get("thread_id", "")
    ikey = envelope.get("idempotency_key", "")

    # A delayed archive (retries can span minutes) must not archive a thread whose
    # session was resumed or restarted in the meantime.
    if session_id:
        async with db_factory() as db:
            status = await db.scalar(
                text("SELECT status FROM sessions WHERE id = :sid"), {"sid": session_id}
            )
        if status in _LIVE_STATUSES:
            logger.info("skipping stale thread archive for live session %s", session_id)
            return True

    async def finalize(db: AsyncSession) -> None:
        await db.execute(
            text("""
                UPDATE sessions
                SET discord_thread_archived_at = now(), thread_archive_requested_at = now()
                WHERE id = :sid
            """),
            {"sid": session_id},
        )

    await _claim_act_finalize(
        db_factory,
        etype="discord.archive_thread.v1",
        ikey=ikey,
        session_id=session_id,
        side_effect=lambda: archive_discord_thread(thread_id),
        finalize=finalize,
    )
    return True
