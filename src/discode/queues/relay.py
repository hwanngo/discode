from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.db.models import RedisOutbox

logger = logging.getLogger(__name__)

# Config knobs (loaded from env or defaults)
OUTBOX_BATCH_SIZE = 64
OUTBOX_IDLE_SLEEP_MS = 200
OUTBOX_MAX_ATTEMPTS = 50
OUTBOX_BASE_BACKOFF_MS = 100
OUTBOX_MAX_BACKOFF_MS = 60_000


def _backoff_ms(attempts: int) -> int:
    """Exponential backoff capped at OUTBOX_MAX_BACKOFF_MS."""
    ms: int = OUTBOX_BASE_BACKOFF_MS * (2**attempts)
    result: int = min(ms, OUTBOX_MAX_BACKOFF_MS)
    return result


class RelayLoop:
    """Drains unpublished redis_outbox rows for a given producer to Redis Streams.

    Each row is published via XADD to its stream_key, then marked published_at=now().
    On failure: exponential backoff, increment attempts. After OUTBOX_MAX_ATTEMPTS: mark error.
    """

    def __init__(
        self,
        producer: str,
        redis_client: Any,  # redis.asyncio.Redis
        db_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.producer = producer
        self.redis = redis_client
        self.db_factory = db_factory
        self._running = False

    async def run(self) -> None:
        """Main drain loop. Runs until cancelled."""
        self._running = True
        try:
            while self._running:
                drained = await self._drain_batch()
                if not drained:
                    await asyncio.sleep(OUTBOX_IDLE_SLEEP_MS / 1000)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("relay loop error for producer %s", self.producer)
            raise

    async def _drain_batch(self) -> int:
        """Drain up to OUTBOX_BATCH_SIZE pending rows. Returns count drained."""
        now = datetime.now(tz=UTC)

        async with self.db_factory() as session:
            async with session.begin():
                stmt = (
                    select(RedisOutbox)
                    .where(
                        RedisOutbox.producer == self.producer,
                        RedisOutbox.published_at.is_(None),
                        RedisOutbox.next_attempt_at <= now,
                        RedisOutbox.attempts < OUTBOX_MAX_ATTEMPTS,
                    )
                    .order_by(RedisOutbox.id)
                    .limit(OUTBOX_BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()

                if not rows:
                    return 0

                drained = 0
                for row in rows:
                    try:
                        fields = {
                            "payload": json.dumps(row.payload),
                            "envelope_type": row.envelope_type,
                        }
                        await self.redis.xadd(row.stream_key, fields)
                        await session.execute(
                            update(RedisOutbox)
                            .where(RedisOutbox.id == row.id)
                            .values(published_at=datetime.now(tz=UTC))
                        )
                        drained += 1
                    except Exception as e:
                        new_attempts = row.attempts + 1
                        backoff_delta = timedelta(milliseconds=_backoff_ms(new_attempts))
                        next_attempt = datetime.now(tz=UTC) + backoff_delta

                        if new_attempts >= OUTBOX_MAX_ATTEMPTS:
                            error_msg = "max_attempts"
                        else:
                            error_msg = str(e)

                        await session.execute(
                            update(RedisOutbox)
                            .where(RedisOutbox.id == row.id)
                            .values(
                                attempts=new_attempts,
                                next_attempt_at=next_attempt,
                                error=error_msg,
                            )
                        )
                        logger.warning(
                            "relay xadd failed for row %d (attempts=%d): %s",
                            row.id,
                            new_attempts,
                            e,
                        )

                return drained

    async def drain_once(self) -> int:
        """Drain one batch. Used in tests."""
        return await self._drain_batch()
