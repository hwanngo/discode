from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

RUNNER_JOB_PEL_IDLE_MS = 60_000
RUNNER_JOB_MAX_DELIVERIES = 5


class RunnerConsumerGroup:
    """XREADGROUP + XAUTOCLAIM consumer for runner:jobs:<host_id> and input:jobs:<host_id>."""

    def __init__(self, redis_client: Any, stream_key: str, group: str, consumer: str) -> None:
        self.redis = redis_client
        self.stream_key = stream_key
        self.group = group
        self.consumer = consumer

    async def ensure_group(self) -> None:
        """Create consumer group if it doesn't exist (MKSTREAM)."""
        try:
            await self.redis.xgroup_create(self.stream_key, self.group, id="0", mkstream=True)
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def read_new(
        self, count: int = 1, block_ms: int = 1000
    ) -> list[tuple[str, dict[str, str]]]:
        """XREADGROUP '>': returns [(message_id, fields_dict), ...]"""
        results = await self.redis.xreadgroup(
            self.group, self.consumer, {self.stream_key: ">"}, count=count, block=block_ms
        )
        if not results:
            return []
        _, messages = results[0]
        return [
            (msg_id.decode(), {k.decode(): v.decode() for k, v in fields.items()})
            for msg_id, fields in messages
        ]

    async def ack(self, message_id: str) -> None:
        """XACK the message."""
        await self.redis.xack(self.stream_key, self.group, message_id)

    async def delivery_count(self, message_id: str) -> int:
        """Return the XPENDING delivery count for a specific message_id, or 0."""
        try:
            result = await self.redis.xpending_range(
                self.stream_key,
                self.group,
                min=message_id,
                max=message_id,
                count=1,
                idle=0,
            )
        except Exception:
            logger.exception(
                "delivery_count xpending failed stream=%s id=%s",
                self.stream_key,
                message_id,
            )
            return 0
        if not result:
            return 0
        entry = result[0]
        # entry can be a dict (redis-py) or a tuple/list. Try common shapes.
        if isinstance(entry, dict):
            count = entry.get("times_delivered") or entry.get(b"times_delivered") or 0
            try:
                return int(count)
            except TypeError, ValueError:
                return 0
        try:
            # tuple form: (message_id, consumer, idle_ms, times_delivered)
            return int(entry[3])
        except IndexError, TypeError, ValueError:
            return 0

    async def claim_pending(
        self, min_idle_ms: int | None = None
    ) -> list[tuple[str, dict[str, str]]]:
        """XAUTOCLAIM messages idle longer than min_idle_ms. Returns [(msg_id, fields), ...]"""
        idle = min_idle_ms or RUNNER_JOB_PEL_IDLE_MS
        result = await self.redis.xautoclaim(
            self.stream_key,
            self.group,
            self.consumer,
            min_idle_time=idle,
            start_id="0-0",
            count=10,
        )
        # result is (next_start_id, [(msg_id, fields), ...], deleted_ids)
        messages = result[1] if result else []
        return [
            (msg_id.decode(), {k.decode(): v.decode() for k, v in fields.items()})
            for msg_id, fields in messages
        ]
