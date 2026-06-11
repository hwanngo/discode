from __future__ import annotations

import logging
import uuid

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.bot.session_service import bind_session_thread
from discode.db.models import RedisOutbox
from discode.db.models import Session as SessionModel
from discode.queues.outbox import write_outbox

logger = logging.getLogger(__name__)


async def sweep_runner_heartbeat_orphans(
    db_factory: async_sessionmaker[AsyncSession],
    orphan_recovery_ttl_hours: int = 24,
) -> int:
    """
    Runner hosts with stale heartbeat → fail their sessions.
    """
    async with db_factory() as db:
        async with db.begin():
            # Mark stale hosts offline
            await db.execute(
                text(f"""
                    UPDATE runner_hosts
                    SET status = 'offline'
                    WHERE status = 'online'
                      AND last_heartbeat_at < now() - interval '{orphan_recovery_ttl_hours} hours'
                """),
            )

            # Fail sessions on offline hosts whose heartbeat is genuinely stale.
            # A graceful runner restart marks the host offline but its heartbeat
            # is still recent; gating on the same TTL/grace avoids mass-failing
            # every live session on a host that just went offline cleanly.
            result = await db.execute(
                text(f"""
                    UPDATE sessions
                    SET status = 'failed', status_reason = 'runner_lost'
                    WHERE status IN ('running', 'idle', 'resuming')
                      AND host_id IN (
                          SELECT id FROM runner_hosts
                          WHERE status = 'offline'
                            AND last_heartbeat_at
                                < now() - interval '{orphan_recovery_ttl_hours} hours'
                      )
                    RETURNING id, guild_id, thread_id
                """)
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
                        "message": "Runner host lost. Session terminated.",
                        "then_archive": True,
                    },
                )

            return len(rows)


async def sweep_session_thread_binding_drift(
    db_factory: async_sessionmaker[AsyncSession],
    max_attempts: int = 3,
) -> tuple[int, int]:
    """Find sessions whose ``thread_id`` was never bound and attempt to repair them.

    A session is considered "drifted" if it meets either condition:
      - ``thread_id IS NULL`` and ``status IN ('pending', 'running', 'creating')``
      - ``status_reason = 'thread_bind_failed'``

    Repair strategy
    ---------------
    For each drifted session, look for a ``runner.create_session.v1`` outbox row
    whose ``payload.session_id`` matches.  If the outbox payload already has a
    ``thread_id`` field, call :func:`bind_session_thread` to atomically fix the
    session row.

    Exhaustion / dead-lettering
    ---------------------------
    If ``repair_attempts >= max_attempts`` and repair is still not possible,
    mark ``status_reason = 'thread_bind_repair_exhausted'`` and emit a WARNING
    so operators can intervene.

    Returns
    -------
    (repaired_count, dead_lettered_count) — counts for the current sweep pass.
    """
    repaired = 0
    dead_lettered = 0

    # Collect candidate session IDs in one short transaction (no writes yet).
    async with db_factory() as db:
        result = await db.execute(
            sa.select(
                SessionModel.id, SessionModel.repair_attempts, SessionModel.status_reason
            ).where(
                SessionModel.thread_id.is_(None),
                SessionModel.status.in_(["pending", "running", "creating"]),
            )
        )
        candidates = result.fetchall()

    # Also collect sessions flagged with thread_bind_failed that already have
    # a thread_id (shouldn't exist normally, but handle defensively) or that
    # still have thread_id=NULL — unify by session_id set.
    async with db_factory() as db:
        result2 = await db.execute(
            sa.select(
                SessionModel.id, SessionModel.repair_attempts, SessionModel.status_reason
            ).where(
                SessionModel.status_reason == "thread_bind_failed",
                SessionModel.thread_id.is_(None),
            )
        )
        extra = result2.fetchall()

    # Deduplicate by session id
    seen: set[uuid.UUID] = set()
    all_candidates: list[tuple[uuid.UUID, int, str | None]] = []
    for sid, attempts, reason in list(candidates) + list(extra):
        if sid not in seen:
            seen.add(sid)
            all_candidates.append((sid, attempts, reason))

    for session_uuid, repair_attempts, _status_reason in all_candidates:
        try:
            session_id_str = str(session_uuid)

            # --- dead-letter path: already exhausted --------------------------------
            if repair_attempts >= max_attempts:
                async with db_factory() as db:
                    async with db.begin():
                        row = await db.get(SessionModel, session_uuid, with_for_update=True)
                        if row is None or row.thread_id is not None:
                            # Already repaired by a concurrent sweep — skip.
                            continue
                        if row.repair_attempts < max_attempts:
                            # Attempts were decremented externally — skip dead-letter.
                            continue
                        if row.status_reason == "thread_bind_repair_exhausted":
                            # Already dead-lettered — skip.
                            continue
                        row.status_reason = "thread_bind_repair_exhausted"
                        logger.warning(
                            "sweep_session_thread_binding_drift: dead-lettering session "
                            "after max repair attempts",
                            extra={
                                "session_id": session_id_str,
                                "repair_attempts": repair_attempts,
                                "max_attempts": max_attempts,
                            },
                        )
                dead_lettered += 1
                continue

            # --- repair path: look for thread_id in outbox payload ------------------
            thread_id_from_outbox: str | None = None
            async with db_factory() as db:
                outbox_result = await db.execute(
                    sa.select(RedisOutbox).where(
                        RedisOutbox.envelope_type == "runner.create_session.v1",
                        sa.func.json_extract_path_text(RedisOutbox.payload, "session_id")
                        == session_id_str,
                        RedisOutbox.published_at.is_(None),
                    )
                )
                outbox_row = outbox_result.scalar_one_or_none()
                if outbox_row is not None:
                    raw = outbox_row.payload.get("thread_id")
                    thread_id_from_outbox = str(raw) if isinstance(raw, str) else None

            if thread_id_from_outbox:
                # Attempt the bind — bind_session_thread owns its own transaction.
                result_obj = await bind_session_thread(
                    db_factory,
                    session_id=session_id_str,
                    thread_id=thread_id_from_outbox,
                )

                if result_obj.success:
                    # Clear the drift marker on the session row.
                    async with db_factory() as db:
                        async with db.begin():
                            row = await db.get(SessionModel, session_uuid, with_for_update=True)
                            if row is not None and row.status_reason in (
                                "thread_bind_failed",
                                None,
                            ):
                                row.status_reason = None
                                row.repair_attempts = (row.repair_attempts or 0) + 1
                    logger.info(
                        "sweep_session_thread_binding_drift: repaired session",
                        extra={
                            "session_id": session_id_str,
                            "thread_id": thread_id_from_outbox,
                        },
                    )
                    repaired += 1
                    continue

            # Repair wasn't possible this pass — increment attempts counter.
            async with db_factory() as db:
                async with db.begin():
                    row = await db.get(SessionModel, session_uuid, with_for_update=True)
                    if row is None or row.thread_id is not None:
                        continue
                    new_attempts = (row.repair_attempts or 0) + 1
                    row.repair_attempts = new_attempts
                    if row.status_reason not in (
                        "thread_bind_failed",
                        "thread_bind_repair_exhausted",
                    ):
                        row.status_reason = "thread_bind_failed"

                    if new_attempts >= max_attempts:
                        # Dead-letter immediately on this pass.
                        row.status_reason = "thread_bind_repair_exhausted"
                        logger.warning(
                            "sweep_session_thread_binding_drift: dead-lettering session "
                            "after max repair attempts",
                            extra={
                                "session_id": session_id_str,
                                "repair_attempts": new_attempts,
                                "max_attempts": max_attempts,
                            },
                        )
                        dead_lettered += 1
        except Exception:
            logger.exception("sweep: error processing session %s", session_uuid)

    return repaired, dead_lettered
