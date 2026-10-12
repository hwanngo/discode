"""Trim only acknowledged stream history, never unread or pending work."""

from __future__ import annotations

import time
from typing import Any


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _id(value: Any) -> tuple[int, int]:
    milliseconds, sequence = _decode(value).split("-", 1)
    return int(milliseconds), int(sequence)


async def trim_acknowledged(redis: Any, stream: str, retention_hours: int) -> int:
    groups = await redis.xinfo_groups(stream)
    if not groups:
        return 0  # No consumer has established what is safe to remove.
    floor = (max(0, int(time.time() * 1000) - retention_hours * 3600000), 0)
    for info in groups:
        group = info.get("name", info.get(b"name"))
        last = info.get("last-delivered-id", info.get(b"last-delivered-id", b"0-0"))
        floor = min(floor, _id(last))
        pending = await redis.xpending_range(stream, group, min="-", max="+", count=1)
        if pending:
            first = pending[0]
            floor = min(floor, _id(first.get("message_id", first.get(b"message_id"))))
    if floor == (0, 0):
        return 0
    return int(await redis.xtrim(stream, minid=f"{floor[0]}-{floor[1]}", approximate=False))
