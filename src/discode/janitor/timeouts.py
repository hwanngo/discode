from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.config import get_settings
from discode.db.accessors.supersede import release_superseded
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
                      AND created_at < now()
                        - interval '{get_settings().SESSION_CREATE_TIMEOUT_SECONDS} seconds'
                    RETURNING id, guild_id, thread_id
                """),
            )
            rows = result.fetchall()

            for row in rows:
                session_id, guild_id, thread_id = str(row[0]), row[1], row[2] or ""
                await release_superseded(db, session_id)
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
                      AND lifecycle_changed_at < now()
                        - interval '{get_settings().RESUME_STUCK_THRESHOLD_SECONDS} seconds'
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
                    SET status = 'idle', lifecycle_changed_at = now()
                    WHERE status = 'running'
                      AND NOT EXISTS (SELECT 1 FROM input_turns t
                          WHERE t.session_id = sessions.id
                            AND t.lifecycle_version = sessions.lifecycle_version
                            AND t.status IN ('queued', 'executing', 'completed'))
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
                        lifecycle_version = lifecycle_version + 1, lifecycle_changed_at = now(),
                        thread_archive_requested_at = now()
                    WHERE status = 'idle'
                      AND NOT EXISTS (SELECT 1 FROM input_turns t
                          WHERE t.session_id = sessions.id
                            AND t.lifecycle_version = sessions.lifecycle_version
                            AND t.status IN ('queued', 'executing', 'completed'))
                      AND last_activity_at < now() - interval '{archive_timeout_minutes} minutes'
                    RETURNING id, guild_id, thread_id, host_id, lifecycle_version
                """),
            )
            rows = result.fetchall()

            for row in rows:
                session_id, guild_id, thread_id = str(row[0]), row[1], row[2] or ""
                if row[3]:
                    await write_outbox(
                        db,
                        producer="janitor",
                        stream_key=f"runner:jobs:{row[3]}",
                        envelope_type="runner.stop_session.v1",
                        payload={
                            "type": "runner.stop_session.v1",
                            "idempotency_key": str(uuid.uuid4()),
                            "session_id": session_id,
                            "lifecycle_version": row[4],
                            "preserve_archived": True,
                        },
                    )
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
                    SET status = 'stopping', status_reason = 'idle_archive_timeout',
                        lifecycle_version = lifecycle_version + 1, lifecycle_changed_at = now()
                    WHERE status IN ('running', 'idle', 'archived')
                      AND last_activity_at < now() - interval '{stop_timeout_hours} hours'
                      AND NOT EXISTS (SELECT 1 FROM input_turns t
                          WHERE t.session_id = sessions.id
                            AND t.lifecycle_version = sessions.lifecycle_version
                            AND t.status IN ('queued', 'executing', 'completed'))
                    RETURNING id, host_id, lifecycle_version
                """),
            )
            rows = result.fetchall()
            for row in rows:
                if row[1]:
                    await write_outbox(
                        db,
                        producer="janitor",
                        stream_key=f"runner:jobs:{row[1]}",
                        envelope_type="runner.stop_session.v1",
                        payload={
                            "type": "runner.stop_session.v1",
                            "idempotency_key": str(uuid.uuid4()),
                            "session_id": str(row[0]),
                            "lifecycle_version": row[2],
                        },
                    )
                else:
                    await db.execute(
                        text(
                            "UPDATE sessions SET status='stopped', stopped_at=now() WHERE id=:sid"
                        ),
                        {"sid": row[0]},
                    )
            return len(rows)


async def sweep_stopping_watchdog(
    db_factory: async_sessionmaker[AsyncSession],
    threshold_seconds: int = 600,
) -> int:
    """Sessions stuck in 'stopping' (stop job lost or runner gone) → stopped.

    Without this they never leave 'stopping' and permanently count against the
    owner's active-session quota. Bumping lifecycle_version makes a late stop job
    a no-op and fences any in-flight turn.
    """
    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                text("""
                    UPDATE sessions
                    SET status = 'stopped',
                        status_reason = COALESCE(status_reason, 'stop_unconfirmed'),
                        lifecycle_version = lifecycle_version + 1,
                        lifecycle_changed_at = now()
                    WHERE status = 'stopping'
                      AND lifecycle_changed_at < now() - make_interval(secs => :secs)
                    RETURNING id
                """),
                {"secs": threshold_seconds},
            )
            return len(result.fetchall())
