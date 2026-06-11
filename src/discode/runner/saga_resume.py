from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


async def recover_sessions(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    host_id: str,
    session_store: dict[str, dict[str, Any]],
) -> tuple[int, int]:
    """Rehydrate the in-memory session store from DB on runner startup.

    Returns (recovered, failed) counts.
    """
    from discode.runner.saga_create import revalidate_cwd
    from discode.runner.session_store import build_session_store_entry

    async with db_factory() as db:
        result = await db.execute(
            text("""
                SELECT id, tool, cwd_realpath, guild_id, thread_id,
                       tool_resume_token, owner_id
                FROM sessions
                WHERE host_id = :hid AND status IN ('running', 'idle', 'resuming')
            """),
            {"hid": host_id},
        )
        rows = result.fetchall()

    recovered = 0
    failed = 0

    for row in rows:
        session_id = str(row[0])
        try:
            # FIX 3: re-canonicalize + re-check the stored cwd against current
            # allowed roots before rehydrating. A now-invalid or symlink-swapped
            # dir must not be revived; mark the session failed and skip it.
            async with db_factory() as db:
                realpath, deny_reason = await revalidate_cwd(
                    db,
                    cwd=row[2] or "",
                    guild_id=row[3] or "",
                    owner_id=row[6] or "",
                    host_id=host_id,
                )
            if deny_reason is not None:
                logger.warning(
                    "recovery refused for session %s: cwd revalidation failed: %s",
                    session_id,
                    deny_reason,
                )
                async with db_factory() as db:
                    async with db.begin():
                        await db.execute(
                            text(
                                "UPDATE sessions SET status='failed',"
                                " status_reason='cwd_invalid' WHERE id=:sid"
                            ),
                            {"sid": session_id},
                        )
                failed += 1
                continue

            session_store[session_id] = build_session_store_entry(
                tool=row[1],
                cwd=realpath or "",
                env={},
                tool_resume_token=row[5],
                guild_id=row[3],
                thread_id=row[4] or "",
                host_id=host_id,
                status="running",
            )
            recovered += 1
        except Exception:
            logger.exception("recovery failed for session %s", session_id)
            async with db_factory() as db:
                async with db.begin():
                    await db.execute(
                        text(
                            "UPDATE sessions SET status='orphaned',"
                            " status_reason='runner_lost' WHERE id=:sid"
                        ),
                        {"sid": session_id},
                    )
            failed += 1

    return recovered, failed
