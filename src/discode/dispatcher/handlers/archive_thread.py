from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


async def handle_archive_thread(
    envelope: dict[str, Any],
    *,
    db_factory: async_sessionmaker[AsyncSession],
    archive_discord_thread: Callable[..., Awaitable[None]],
) -> bool:
    """
    Handle discord.archive_thread.v1 envelope.

    Idempotent via claim-then-finalize (mirrors runner.saga_input):

    1. Claim the idempotency key with outcome=NULL. The claim dedupes concurrent
       delivery but does NOT mark the work done.
    2. If a finalized claim already exists (outcome IS NOT NULL), this is a
       genuine duplicate -> skip.
    3. Perform the Discord archive side effect.
    4. ONLY after it succeeds, finalize: set outcome AND set
       discord_thread_archived_at.

    If the Discord call fails (or the process crashes) before finalize, the
    claim stays unfinalized, discord_thread_archived_at stays NULL, and
    redelivery legitimately retries (scan_pending_archives can still find it).
    """
    session_id = envelope.get("session_id", "")
    thread_id = envelope.get("thread_id", "")
    ikey = envelope.get("idempotency_key", "")

    # Stage 1: claim (outcome=NULL). RETURNING tells us if we just inserted.
    async with db_factory() as db:
        async with db.begin():
            insert_result = await db.execute(
                text("""
                    INSERT INTO idempotency_keys
                        (envelope_type, idempotency_key, session_id, expires_at)
                    VALUES
                        ('discord.archive_thread.v1', :ikey, :sid,
                         now() + interval '7 days')
                    ON CONFLICT DO NOTHING
                    RETURNING idempotency_key
                """),
                {"ikey": ikey, "sid": session_id},
            )
            freshly_claimed = insert_result.fetchone() is not None

            # Record the request intent so recovery can find pending archives,
            # but do NOT set discord_thread_archived_at until Discord succeeds.
            if freshly_claimed:
                await db.execute(
                    text("""
                        UPDATE sessions
                        SET thread_archive_requested_at = now()
                        WHERE id = :sid
                    """),
                    {"sid": session_id},
                )

    # Stage 2: if an existing claim is already finalized, it's a true duplicate.
    if not freshly_claimed:
        async with db_factory() as db:
            outcome_result = await db.execute(
                text("""
                    SELECT outcome FROM idempotency_keys
                    WHERE envelope_type = 'discord.archive_thread.v1'
                      AND idempotency_key = :ikey
                """),
                {"ikey": ikey},
            )
            row = outcome_result.fetchone()
        if row is not None and row[0] is not None:
            return True  # already finalized -> skip

    # Stage 3: perform the side effect. If this raises, we never finalize, so
    # redelivery retries.
    await archive_discord_thread(thread_id)

    # Stage 4: finalize ONLY after the archive succeeds.
    async with db_factory() as db:
        async with db.begin():
            await db.execute(
                text("""
                    UPDATE idempotency_keys
                    SET outcome = 'done'
                    WHERE envelope_type = 'discord.archive_thread.v1'
                      AND idempotency_key = :ikey
                """),
                {"ikey": ikey},
            )
            await db.execute(
                text("""
                    UPDATE sessions
                    SET discord_thread_archived_at = now(),
                        thread_archive_requested_at = now()
                    WHERE id = :sid
                """),
                {"sid": session_id},
            )
    return True
