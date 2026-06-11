from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


async def scan_pending_archives(
    db_factory: async_sessionmaker[AsyncSession],
) -> list[str]:
    """Return session_ids with pending archive.

    Pending means: thread_archive_requested_at IS NOT NULL
    AND discord_thread_archived_at IS NULL.
    """
    async with db_factory() as db:
        result = await db.execute(
            text("""
                SELECT id FROM sessions
                WHERE thread_archive_requested_at IS NOT NULL
                  AND discord_thread_archived_at IS NULL
            """)
        )
        return [str(row[0]) for row in result.fetchall()]
