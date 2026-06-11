from __future__ import annotations

import pytest_asyncio

from discode.db.engine import make_engine, make_session_factory


@pytest_asyncio.fixture
async def db_factory_fixture(migrated_db_url):
    engine = make_engine(migrated_db_url)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()
