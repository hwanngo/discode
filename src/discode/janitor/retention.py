from __future__ import annotations

import logging
from typing import cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
                    WHERE expires_at < now()
                """)
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
                text(f"""
                    DELETE FROM events
                    WHERE created_at < now() - interval '{retention_days} days'
                """),
            )
            return _rowcount(result)


async def sweep_outbox(
    db_factory: async_sessionmaker[AsyncSession],
    retention_days: int = 7,
) -> int:
    """Delete terminal redis_outbox rows older than the retention window.

    A row is terminal once it has been published, or once its delivery attempts
    are exhausted (error = 'max_attempts'). Such rows are never re-selected by the
    relay, so without a sweep the outbox table grows unbounded. We retain them for
    a window (for debugging / observability) keyed off created_at, then delete.
    """
    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                text(f"""
                    DELETE FROM redis_outbox
                    WHERE (published_at IS NOT NULL OR error = 'max_attempts')
                      AND created_at < now() - interval '{retention_days} days'
                """),
            )
            return _rowcount(result)
