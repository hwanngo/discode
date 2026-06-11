from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from discode.db.models import RedisOutbox


async def write_outbox(
    session: AsyncSession,
    *,
    producer: str,
    stream_key: str,
    envelope_type: str,
    payload: dict,  # type: ignore[type-arg]
) -> RedisOutbox:
    """Insert a redis_outbox row in the caller's transaction. Returns the row."""
    row = RedisOutbox(
        producer=producer,
        stream_key=stream_key,
        envelope_type=envelope_type,
        payload=payload,
    )
    session.add(row)
    await session.flush()
    return row
