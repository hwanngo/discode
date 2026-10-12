from __future__ import annotations

import logging

from discode.config import get_settings
from discode.queues.consumers.runner_cg import RunnerConsumerGroup

logger = logging.getLogger(__name__)

DISPATCHER_STREAM = "discord:outbound"
DISPATCHER_GROUP = "dispatcher-cg"
DISPATCHER_PEL_IDLE_MS = 60_000
DISPATCHER_MAX_DELIVERIES = 5


class DispatcherConsumer:
    """Reads from discord:outbound consumer group."""

    def __init__(self, redis_client: object, consumer_id: str) -> None:
        self._cg = RunnerConsumerGroup(
            redis_client, DISPATCHER_STREAM, DISPATCHER_GROUP, consumer_id
        )
        self._redis = redis_client

    async def ensure_group(self) -> None:
        await self._cg.ensure_group()

    async def read_next(self) -> list[tuple[str, dict[str, str]]]:
        """Read new messages from the stream."""
        return await self._cg.read_new(count=10, block_ms=100)

    async def claim_pending(self) -> list[tuple[str, dict[str, str]]]:
        """Claim idle pending messages."""
        return await self._cg.claim_pending(min_idle_ms=get_settings().DISPATCHER_PEL_IDLE_MS)

    async def ack(self, message_id: str) -> None:
        await self._cg.ack(message_id)

    async def delivery_count(self, message_id: str) -> int:
        """Return the XPENDING delivery count for a specific message_id, or 0."""
        return await self._cg.delivery_count(message_id)

    async def dead_letter(self, message_id: str, fields: dict[str, str]) -> None:
        """Atomically retain failed work before acknowledging its stream entry."""
        import json
        from typing import cast

        from redis.asyncio import Redis

        redis = cast(Redis, self._redis)
        async with redis.pipeline(transaction=True) as pipe:
            pipe.hset(DISPATCHER_DEADLETTERS, message_id, json.dumps(fields))
            pipe.xack(DISPATCHER_STREAM, DISPATCHER_GROUP, message_id)
            await pipe.execute()


DISPATCHER_DEADLETTERS = "discode:deadletter:discord"
