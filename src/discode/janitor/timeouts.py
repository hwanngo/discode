from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.queues.outbox import write_outbox

logger = logging.getLogger(__name__)

SESSION_CREATE_TIMEOUT_SECONDS = 60
RESUME_STUCK_THRESHOLD_SECONDS = 300


async def sweep_creating_watchdog(
    db_factory: async_sessionmaker[AsyncSession],
) -> int:
    """
    Sessions stuck in 'creating' past SESSION_CREATE_TIMEOUT_SECONDS → fail them.
    Returns count of sessions failed.
    """
    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                text(f"""
                    UPDATE sessions
                    SET status = 'failed', status_reason = 'creation_timeout'
                    WHERE status = 'creating'
                      AND created_at < now() - interval '{SESSION_CREATE_TIMEOUT_SECONDS} seconds'
                    RETURNING id, guild_id, thread_id
                """),
            )
            rows = result.fetchall()

            for row in rows:
                session_id, guild_id, thread_id = str(row[0]), row[1], row[2] or ""
                await write_outbox(
                    db,
                    producer="janitor",
                    stream_key="discord:outbound",
                    envelope_type="discord.system_notice.v1",
                    payload={
                        "type": "discord.system_notice.v1",
                        "idempotency_key": str(uuid.uuid4()),
                        "session_id": session_id,
                        "guild_id": guild_id,
                        "thread_id": thread_id,
                        "notice_type": "session_create_failed",
                        "order_after_seq": 0,
                        "message": "creation_timeout",
                    },
                )

            return len(rows)


async def sweep_resuming_watchdog(
    db_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Sessions stuck in 'resuming' past RESUME_STUCK_THRESHOLD_SECONDS → orphaned."""
    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                text(f"""
                    UPDATE sessions
                    SET status = 'orphaned', status_reason = 'resume_stuck'
                    WHERE status = 'resuming'
                      AND last_activity_at < now()
                        - interval '{RESUME_STUCK_THRESHOLD_SECONDS} seconds'
                    RETURNING id, guild_id, thread_id
                """),
            )
            rows = result.fetchall()

            for row in rows:
                session_id, guild_id, thread_id = str(row[0]), row[1], row[2] or ""
                await write_outbox(
                    db,
                    producer="janitor",
                    stream_key="discord:outbound",
                    envelope_type="discord.terminal_notice.v1",
                    payload={
                        "type": "discord.terminal_notice.v1",
                        "idempotency_key": str(uuid.uuid4()),
                        "session_id": session_id,
                        "guild_id": guild_id,
                        "thread_id": thread_id,
                        "message": "Session resume timed out.",
                        "then_archive": True,
                    },
                )

            return len(rows)


async def sweep_idle_timeout(
    db_factory: async_sessionmaker[AsyncSession],
    idle_timeout_minutes: int = 60,
) -> int:
    """running sessions idle > idle_timeout_minutes → 'idle'."""
    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                text(f"""
                    UPDATE sessions
                    SET status = 'idle'
                    WHERE status = 'running'
                      AND last_activity_at < now() - interval '{idle_timeout_minutes} minutes'
                    RETURNING id
                """),
            )
            return len(result.fetchall())


async def sweep_archive_timeout(
    db_factory: async_sessionmaker[AsyncSession],
    archive_timeout_minutes: int = 120,
) -> int:
    """idle sessions > archive_timeout_minutes → enqueue archive."""
    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                text(f"""
                    UPDATE sessions
                    SET status = 'archived', archived_at = now(),
                        thread_archive_requested_at = now()
                    WHERE status = 'idle'
                      AND last_activity_at < now() - interval '{archive_timeout_minutes} minutes'
                    RETURNING id, guild_id, thread_id
                """),
            )
            rows = result.fetchall()

            for row in rows:
                session_id, guild_id, thread_id = str(row[0]), row[1], row[2] or ""
                await write_outbox(
                    db,
                    producer="janitor",
                    stream_key="discord:outbound",
                    envelope_type="discord.archive_thread.v1",
                    payload={
                        "type": "discord.archive_thread.v1",
                        "idempotency_key": str(uuid.uuid4()),
                        "session_id": session_id,
                        "guild_id": guild_id,
                        "thread_id": thread_id,
                    },
                )

            return len(rows)


async def sweep_stop_timeout(
    db_factory: async_sessionmaker[AsyncSession],
    stop_timeout_hours: int = 24,
) -> int:
    """Any active session > stop_timeout_hours → stopping."""
    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                text(f"""
                    UPDATE sessions
                    SET status = 'stopping', status_reason = 'idle_archive_timeout'
                    WHERE status IN ('running', 'idle', 'archived')
                      AND created_at < now() - interval '{stop_timeout_hours} hours'
                    RETURNING id
                """),
            )
            return len(result.fetchall())
