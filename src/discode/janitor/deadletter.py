from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.config import get_settings
from discode.db.models import RedisOutbox
from discode.metrics import REGISTRY, record_outbox_lag


async def scan_input_dead_letters(
    redis_client: object, db_factory: async_sessionmaker[AsyncSession]
) -> int:
    """Export durable exhausted-outbox count; retain rows for operator recovery.

    No automatic replay: an input may already have caused external side effects.
    Recovery is an explicit, reviewed operation in scripts/recover_outbox.py.
    Returns 0: this only exports gauges and changes no rows.
    """
    async with db_factory() as db:
        count = int(
            await db.scalar(
                select(func.count())
                .select_from(RedisOutbox)
                .where(
                    RedisOutbox.published_at.is_(None),
                    RedisOutbox.attempts >= get_settings().OUTBOX_MAX_ATTEMPTS,
                )
            )
            or 0
        )
        pending, oldest = (
            await db.execute(
                select(func.count(), func.min(RedisOutbox.created_at)).where(
                    RedisOutbox.published_at.is_(None)
                )
            )
        ).one()
        record_outbox_lag(
            int(pending), (datetime.now(UTC) - oldest).total_seconds() if oldest else 0
        )
    REGISTRY.set_gauge("discode_deadletter_outbox", count)
    return 0
