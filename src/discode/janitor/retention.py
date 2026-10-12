from __future__ import annotations

import logging
from typing import cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.config import get_settings

logger = logging.getLogger(__name__)


def _rowcount(result: object) -> int:
    """Return the affected-row count from a DELETE/UPDATE Core result.

    ``AsyncSession.execute(text(...))`` is typed as ``Result[Any]`` (no
    ``rowcount``), but a DML statement always returns a ``CursorResult`` at
    runtime. Centralize the cast so the call sites stay readable and typed.
    """
    return cast("CursorResult[object]", result).rowcount


async def sweep_idempotency_keys(
    db_factory: async_sessionmaker[AsyncSession],
    input_retention_days: int = 30,
) -> int:
    """Clean up expired idempotency keys."""
    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                text("""
                    DELETE FROM idempotency_keys
                    WHERE (envelope_type, idempotency_key) IN (
                        SELECT envelope_type, idempotency_key FROM idempotency_keys
                        WHERE expires_at < now() ORDER BY expires_at LIMIT :batch
                    )
                """),
                {"batch": get_settings().RETENTION_BATCH_SIZE},
            )
            return _rowcount(result)


async def sweep_events(
    db_factory: async_sessionmaker[AsyncSession],
    retention_days: int = 90,
) -> int:
    """Delete old audit events."""
    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                text("""
                    DELETE FROM events
                    WHERE id IN (SELECT id FROM events
                        WHERE created_at < now() - make_interval(days => :days)
                        ORDER BY created_at LIMIT :batch)
                """),
                {"days": retention_days, "batch": get_settings().RETENTION_BATCH_SIZE},
            )
            return _rowcount(result)


async def sweep_outbox(
    db_factory: async_sessionmaker[AsyncSession],
    retention_days: int = 7,
) -> int:
    """Delete terminal redis_outbox rows older than the retention window.

    Published rows expire after the configured window. Exhausted and pending
    rows remain available for explicit operator review and recovery. Each pass
    has a fixed batch budget to bound transaction duration.
    """
    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                text("""
                    DELETE FROM redis_outbox
                    WHERE id IN (SELECT id FROM redis_outbox
                        WHERE published_at IS NOT NULL
                          AND created_at < now() - make_interval(days => :days)
                        ORDER BY created_at LIMIT :batch)
                """),
                {"days": retention_days, "batch": get_settings().RETENTION_BATCH_SIZE},
            )
            return _rowcount(result)


async def sweep_turn_results(db_factory: async_sessionmaker[AsyncSession]) -> int:
    """Expire delivered output, preserving execution tombstones against replay.

    Queued, executing, uncertain and completed-but-undelivered results are retained.
    """
    retention = get_settings().INPUT_IDEMPOTENCY_RETENTION
    if retention == "never":
        return 0
    async with db_factory() as db, db.begin():
        result = await db.execute(
            text("""
            UPDATE input_turns SET result_text=NULL, resume_token=NULL
            WHERE idempotency_key IN (
                SELECT idempotency_key FROM input_turns
                WHERE status IN ('delivered','delivery_failed')
                  AND (result_text IS NOT NULL OR resume_token IS NOT NULL)
                  AND updated_at < now() - make_interval(days => :days)
                ORDER BY updated_at LIMIT :batch
            )
        """),
            {"days": int(retention[:-1]), "batch": get_settings().RETENTION_BATCH_SIZE},
        )
        return _rowcount(result)
