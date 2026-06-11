import uuid

import pytest
import pytest_asyncio

from discode.db.models import Guild, Session, SessionConfig


def _uid() -> str:
    return uuid.uuid4().hex


@pytest_asyncio.fixture
async def seed_session(db_session):
    """Insert a minimal Session row and yield it."""
    guild_id = f"g-{_uid()[:8]}"
    db_session.add(Guild(id=guild_id, name=guild_id))
    await db_session.flush()

    session = Session(
        guild_id=guild_id,
        tool="claude",
        name="test-session",
        cwd="/tmp",
        cwd_realpath="/tmp",
        hook_secret_hash="hash",
        status="creating",
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return session


@pytest.mark.asyncio
async def test_session_config_round_trip(db_session, seed_session):
    sc = SessionConfig(
        session_id=str(seed_session.id),
        key="model",
        value="claude-3-5-haiku",
        value_ciphertext=None,
    )
    db_session.add(sc)
    await db_session.commit()
    await db_session.refresh(sc)
    assert sc.value == "claude-3-5-haiku"
    assert sc.value_ciphertext is None
    assert sc.created_at is not None
