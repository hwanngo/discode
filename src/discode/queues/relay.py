from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.config import get_settings
from discode.db.models import RedisOutbox
from discode.metrics import record_outbox_publish

logger = logging.getLogger(__name__)

# Pause between batches while Redis is unreachable.
_REDIS_UNAVAILABLE_SLEEP_SECONDS = 1.0

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
        self._redis_unavailable = False
        self.settings = get_settings()

    async def run(self) -> None:
        """Main drain loop. Runs until cancelled."""
        self._running = True
        try:
            while self._running:
                drained = await self._drain_batch()
                if self._redis_unavailable:
                    await asyncio.sleep(_REDIS_UNAVAILABLE_SLEEP_SECONDS)
                elif not drained:
                    await asyncio.sleep(self.settings.OUTBOX_IDLE_SLEEP_MS / 1000)
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
                        RedisOutbox.attempts < self.settings.OUTBOX_MAX_ATTEMPTS,
                    )
                    .order_by(RedisOutbox.id)
                    .limit(self.settings.OUTBOX_BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()

                if not rows:
                    return 0

                drained = 0
                self._redis_unavailable = False
                for row in rows:
                    fields = {
                        "payload": json.dumps(row.payload),
                        "envelope_type": row.envelope_type,
                    }
                    try:
                        await self.redis.xadd(row.stream_key, fields)
                    except (RedisConnectionError, RedisTimeoutError) as e:
                        # Redis is unreachable: not this row's fault, so don't spend its
                        # attempt budget. Commit what was published and pause.
                        logger.warning("relay: redis unavailable, pausing: %s", e)
                        self._redis_unavailable = True
                        break
                    except Exception as e:
                        await self._record_failure(session, row, e)
                        continue
                    await session.execute(
                        update(RedisOutbox)
                        .where(RedisOutbox.id == row.id)
                        .values(published_at=datetime.now(tz=UTC))
                    )
                    drained += 1
                    record_outbox_publish(self.producer)

                return drained

    async def _record_failure(self, session: AsyncSession, row: RedisOutbox, e: Exception) -> None:
        new_attempts = row.attempts + 1
        backoff_delta = timedelta(
            milliseconds=min(
                self.settings.OUTBOX_BASE_BACKOFF_MS * 2 ** min(new_attempts, 30),
                self.settings.OUTBOX_MAX_BACKOFF_MS,
            )
        )
        error_msg = "max_attempts" if new_attempts >= self.settings.OUTBOX_MAX_ATTEMPTS else str(e)
        await session.execute(
            update(RedisOutbox)
            .where(RedisOutbox.id == row.id)
            .values(
                attempts=new_attempts,
                next_attempt_at=datetime.now(tz=UTC) + backoff_delta,
                error=error_msg,
            )
        )
        logger.warning("relay xadd failed for row %d (attempts=%d): %s", row.id, new_attempts, e)

    async def drain_once(self) -> int:
        """Drain one batch. Used in tests."""
        return await self._drain_batch()


# Backoff bounds (seconds) for restarting the relay after an unexpected crash.
RELAY_RESTART_BASE_BACKOFF_SECONDS = 1.0
RELAY_RESTART_MAX_BACKOFF_SECONDS = 30.0


async def supervise_relay(relay: RelayLoop) -> None:
    """Run ``relay.run()`` forever, restarting it with backoff if it crashes.

    ``RelayLoop.run()`` re-raises on non-row errors (e.g. a transient DB blip).
    Without supervision a single transient failure would silently kill the relay
    and leave outbox rows unpublished forever. This wrapper logs the exception and
    restarts the relay, applying exponential backoff so a persistently-failing
    relay does not hot-loop. ``CancelledError`` propagates so shutdown still works.
    """
    backoff = RELAY_RESTART_BASE_BACKOFF_SECONDS
    while True:
        try:
            await relay.run()
            # run() returned without raising — it only does so when stopped
            # intentionally; treat as a clean exit and stop supervising.
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("relay crashed; restarting in %.1fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RELAY_RESTART_MAX_BACKOFF_SECONDS)
