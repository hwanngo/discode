from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from discode.bot.commands.session_reads import get_last_session, get_sessions_for_user
from discode.db.engine import make_engine, make_session_factory
from discode.db.models import (
    Guild,
    RunnerHost,
    SessionMember,
    User,
)
from discode.db.models import Session as SessionModel


@pytest_asyncio.fixture
async def db_factory_fixture(migrated_db_url):
    engine = make_engine(migrated_db_url)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


def _unique_ids():
    suffix = uuid.uuid4().hex[:8]
    return (
        f"guild-{suffix}",
        f"user-{suffix}",
        f"host-{suffix}",
    )


async def _seed_prerequisites(db_factory, guild_id, user_id, host_id):
    async with db_factory() as db:
        db.add(Guild(id=guild_id, name="Test Guild"))
        db.add(User(id=user_id, username="testuser"))
        db.add(RunnerHost(id=host_id, status="online"))
        await db.commit()


async def _seed_session(
    db_factory,
    guild_id: str,
    user_id: str,
    host_id: str,
    status: str = "running",
    add_member: bool = True,
) -> str:
    """Insert a Session row (and optionally a membership) and return UUID string."""
    session_id = uuid.uuid4()

    async with db_factory() as db:
        session = SessionModel(
            id=session_id,
            guild_id=guild_id,
            owner_id=user_id,
            tool="claude",
            name="test-session",
            cwd="/home/user/project",
            cwd_realpath="/home/user/project",
            host_id=host_id,
            hook_secret_hash="deadbeef" * 8,
            status=status,
        )
        db.add(session)
        if add_member:
            db.add(SessionMember(session_id=session_id, user_id=user_id, role="owner"))
        await db.commit()

    return str(session_id)


# ---------------------------------------------------------------------------
# get_sessions_for_user tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sessions_for_user_returns_only_member_sessions(db_factory_fixture):
    """get_sessions_for_user returns only sessions where user is a member."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    # Session where user is a member
    member_session_id = await _seed_session(
        db_factory_fixture, guild_id, user_id, host_id, status="running"
    )

    # Another user's session (no membership for user_id)
    other_user_id = f"other-{uuid.uuid4().hex[:8]}"
    async with db_factory_fixture() as db:
        db.add(User(id=other_user_id, username="otheruser"))
        await db.commit()
    other_session_id = await _seed_session(
        db_factory_fixture,
        guild_id,
        other_user_id,
        host_id,
        status="running",
        add_member=False,
    )
    # seed other user's membership separately
    async with db_factory_fixture() as db:
        db.add(
            SessionMember(
                session_id=uuid.UUID(other_session_id),
                user_id=other_user_id,
                role="owner",
            )
        )
        await db.commit()

    sessions = await get_sessions_for_user(db_factory_fixture, guild_id, user_id)

    session_ids = [s["session_id"] for s in sessions]
    assert member_session_id in session_ids
    assert other_session_id not in session_ids


@pytest.mark.asyncio
async def test_get_sessions_for_user_include_all_returns_guild_sessions(db_factory_fixture):
    """get_sessions_for_user with include_all=True returns all guild sessions."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    other_user_id = f"other-{uuid.uuid4().hex[:8]}"
    async with db_factory_fixture() as db:
        db.add(User(id=other_user_id, username="otheruser"))
        await db.commit()

    session_id_1 = await _seed_session(
        db_factory_fixture, guild_id, user_id, host_id, status="running"
    )
    session_id_2 = await _seed_session(
        db_factory_fixture,
        guild_id,
        other_user_id,
        host_id,
        status="running",
        add_member=False,
    )
    async with db_factory_fixture() as db:
        db.add(
            SessionMember(
                session_id=uuid.UUID(session_id_2),
                user_id=other_user_id,
                role="owner",
            )
        )
        await db.commit()

    sessions = await get_sessions_for_user(db_factory_fixture, guild_id, user_id, include_all=True)

    session_ids = [s["session_id"] for s in sessions]
    assert session_id_1 in session_ids
    assert session_id_2 in session_ids


# ---------------------------------------------------------------------------
# get_last_session tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_last_session_returns_most_recent(db_factory_fixture):
    """get_last_session returns the most recently created session for the user."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    # Create two sessions; the second is the most recent
    await _seed_session(db_factory_fixture, guild_id, user_id, host_id, status="running")
    second_id = await _seed_session(
        db_factory_fixture, guild_id, user_id, host_id, status="running"
    )

    last = await get_last_session(db_factory_fixture, guild_id, user_id)

    assert last is not None
    # The second session should be most recent (higher created_at due to insert order)
    assert last["session_id"] == second_id


@pytest.mark.asyncio
async def test_get_last_session_returns_none_when_no_sessions(db_factory_fixture):
    """get_last_session returns None when user has no sessions."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    last = await get_last_session(db_factory_fixture, guild_id, user_id)

    assert last is None
