from __future__ import annotations

import pytest
from sqlalchemy import text

from discode.db.engine import make_engine, make_session_factory


@pytest.mark.asyncio
async def test_engine_connects_and_executes(db_url: str) -> None:
    """make_engine + make_session_factory + await session.execute(text('select 1')) succeeds."""
    engine = make_engine(db_url)
    try:
        factory = make_session_factory(engine)
        async with factory() as session:
            result = await session.execute(text("SELECT 1"))
            row = result.scalar()
            assert row == 1
    finally:
        await engine.dispose()


def test_engine_has_pool_pre_ping(db_url: str) -> None:
    """Engine is created with pool_pre_ping=True."""
    engine = make_engine(db_url)
    assert engine.pool._pre_ping is True  # type: ignore[attr-defined]
