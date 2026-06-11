from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def acquire_advisory_lock(session: AsyncSession, lock_id: int) -> bool:
    """Try pg_try_advisory_lock(lock_id). Returns True if acquired, False otherwise."""
    result = await session.execute(
        text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id}
    )
    row = result.scalar()
    return bool(row)


async def release_advisory_lock(session: AsyncSession, lock_id: int) -> None:
    """Release the advisory lock."""
    await session.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})
