from __future__ import annotations

import pytest

from discode.db.advisory_lock import acquire_advisory_lock, release_advisory_lock
from discode.db.engine import make_engine, make_session_factory


@pytest.mark.asyncio
async def test_advisory_lock_exclusive(db_url: str) -> None:
    """Two concurrent acquires on the same lock_id — one succeeds, other returns False."""
    engine = make_engine(db_url)
    try:
        factory = make_session_factory(engine)

        async with factory() as session1:
            async with factory() as session2:
                # Begin transactions so advisory locks are session-scoped
                await session1.begin()
                await session2.begin()

                lock_id = 999_001

                # First acquire should succeed
                acquired1 = await acquire_advisory_lock(session1, lock_id)
                assert acquired1 is True

                # Second acquire on same lock_id in different session should fail
                acquired2 = await acquire_advisory_lock(session2, lock_id)
                assert acquired2 is False

                # Release from session1
                await release_advisory_lock(session1, lock_id)

                # Now session2 should be able to acquire it
                acquired3 = await acquire_advisory_lock(session2, lock_id)
                assert acquired3 is True

                await release_advisory_lock(session2, lock_id)

                await session1.rollback()
                await session2.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_janitor_sweep_lock_blocks_second_instance(db_url: str) -> None:
    """A second janitor cannot acquire the sweep lock while the first holds it."""
    from discode.janitor.main import JANITOR_SWEEP_LOCK_ID

    engine = make_engine(db_url)
    try:
        factory = make_session_factory(engine)
        async with factory() as s1, factory() as s2:
            await s1.begin()
            await s2.begin()

            assert await acquire_advisory_lock(s1, JANITOR_SWEEP_LOCK_ID) is True
            # Second instance is locked out.
            assert await acquire_advisory_lock(s2, JANITOR_SWEEP_LOCK_ID) is False

            await release_advisory_lock(s1, JANITOR_SWEEP_LOCK_ID)
            await s1.rollback()
            await s2.rollback()
    finally:
        await engine.dispose()
