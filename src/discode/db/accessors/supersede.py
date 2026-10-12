"""Resume-by-name bookkeeping shared by the bot, runner and janitor."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SUPERSEDED_PREFIX = "superseded"


def superseded_by(session_id: str) -> str:
    """status_reason marking an archived session as resumed by ``session_id``."""
    return f"{SUPERSEDED_PREFIX}:{session_id}"


async def release_superseded(db: AsyncSession, failed_session_id: str) -> None:
    """Make a predecessor resumable again when its successor failed to start."""
    await db.execute(
        text(
            "UPDATE sessions SET status_reason = NULL "
            "WHERE status = 'archived' AND status_reason = :reason"
        ),
        {"reason": superseded_by(failed_session_id)},
    )
