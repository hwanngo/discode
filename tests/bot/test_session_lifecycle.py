"""Tests for /stop /archive /resume /restart session lifecycle sagas."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from discode.bot.session_service import (
    archive_session,
    resolve_session_target,
    restart_session,
    resume_session,
    stop_session,
)
from discode.db.engine import make_engine, make_session_factory
from discode.db.models import Session as SessionModel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_factory(migrated_db_url: str):
    """A committing session factory for lifecycle tests."""
    engine = make_engine(migrated_db_url)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def live_session(db_factory):
    """Insert a real committed Session row with status='idle' for lifecycle tests.

    owner_id is left NULL to avoid the users FK constraint in tests.
    """
    guild_id = f"guild-{uuid.uuid4().hex[:8]}"
    sid = uuid.uuid4()

    from discode.db.models import Guild
    from discode.db.models.guild import User

    _owner_id = f"user-{sid.hex[:8]}"

    async with db_factory() as db:
        async with db.begin():
            db.add(Guild(id=guild_id, name="Lifecycle Test Guild"))
            db.add(User(id=_owner_id, username="testuser"))
            sess = SessionModel(
                id=sid,
                guild_id=guild_id,
                owner_id=_owner_id,
                tool="claude",
                name=f"test-{sid.hex[:6]}",
                cwd="/tmp",
                cwd_realpath="/tmp",
                hook_secret_hash="fakehash",
                status="idle",
            )
            db.add(sess)

    # Fetch row name for the namespace
    async with db_factory() as db:
        row = await db.get(SessionModel, sid)
        _name = row.name

    # yield a simple namespace so tests can access .id, .guild_id, etc.
    import types

    ns = types.SimpleNamespace(
        id=sid,
        guild_id=guild_id,
        owner_id=_owner_id,
        name=_name,
    )
    yield ns

    # Cleanup: delete the session and guild after each test
    async with db_factory() as db:
        async with db.begin():
            row = await db.get(SessionModel, sid)
            if row is not None:
                await db.delete(row)
            from discode.db.models import Guild as GuildModel
            from discode.db.models.guild import User as UserModel

            u = await db.get(UserModel, _owner_id)
            if u is not None:
                await db.delete(u)
            g = await db.get(GuildModel, guild_id)
            if g is not None:
                await db.delete(g)


# ---------------------------------------------------------------------------
# resolve_session_target
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_by_id(db_factory, live_session):
    target = await resolve_session_target(
        db_factory,
        guild_id=live_session.guild_id,
        user_id=live_session.owner_id,
        session_ref=str(live_session.id),
    )
    assert target is not None
    assert target["session_id"] == str(live_session.id)
    assert "thread_id" in target
    assert "owner_id" in target
    assert target["owner_id"] == live_session.owner_id


@pytest.mark.asyncio
async def test_resolve_by_name(db_factory, live_session):
    target = await resolve_session_target(
        db_factory,
        guild_id=live_session.guild_id,
        user_id=live_session.owner_id,
        session_ref=live_session.name,
    )
    assert target is not None
    assert target["name"] == live_session.name
    assert "thread_id" in target
    assert "owner_id" in target
    assert target["owner_id"] == live_session.owner_id


@pytest.mark.asyncio
async def test_resolve_none_returns_most_recent(db_factory, live_session):
    target = await resolve_session_target(
        db_factory,
        guild_id=live_session.guild_id,
        user_id=live_session.owner_id,
        session_ref=None,
    )
    assert target is not None
    assert "thread_id" in target
    assert "owner_id" in target
    assert target["owner_id"] == live_session.owner_id


@pytest.mark.asyncio
async def test_resolve_unknown_returns_none(db_factory):
    target = await resolve_session_target(
        db_factory,
        guild_id="g-definitely-does-not-exist",
        user_id="u-nope",
        session_ref=None,
    )
    assert target is None


# ---------------------------------------------------------------------------
# stop_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_session_marks_stopped(db_factory, live_session):
    ok = await stop_session(
        db_factory,
        session_id=str(live_session.id),
        guild_id=live_session.guild_id,
        requested_by_id="u",
    )
    assert ok is True

    async with db_factory() as db:
        row = await db.get(SessionModel, live_session.id)
    assert row is not None
    assert row.status == "stopped"
    assert row.stopped_at is not None


@pytest.mark.asyncio
async def test_stop_session_wrong_guild_returns_false(db_factory, live_session):
    ok = await stop_session(
        db_factory,
        session_id=str(live_session.id),
        guild_id="wrong-guild",
        requested_by_id="u",
    )
    assert ok is False


# ---------------------------------------------------------------------------
# archive_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_session_marks_archived(db_factory, live_session):
    ok = await archive_session(
        db_factory,
        session_id=str(live_session.id),
        guild_id=live_session.guild_id,
    )
    assert ok is True

    async with db_factory() as db:
        row = await db.get(SessionModel, live_session.id)
    assert row is not None
    assert row.status == "archived"
    assert row.archived_at is not None


@pytest.mark.asyncio
async def test_archive_already_archived_returns_false(db_factory, live_session):
    await archive_session(
        db_factory,
        session_id=str(live_session.id),
        guild_id=live_session.guild_id,
    )
    # Second call should return False (already archived, not in allowed states)
    ok = await archive_session(
        db_factory,
        session_id=str(live_session.id),
        guild_id=live_session.guild_id,
    )
    assert ok is False


# ---------------------------------------------------------------------------
# resume_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_session_archived_to_running(db_factory, live_session):
    # First archive it
    await archive_session(
        db_factory,
        session_id=str(live_session.id),
        guild_id=live_session.guild_id,
    )
    # Then resume
    ok = await resume_session(
        db_factory,
        session_id=str(live_session.id),
        guild_id=live_session.guild_id,
        host_id="h",
    )
    assert ok is True

    async with db_factory() as db:
        row = await db.get(SessionModel, live_session.id)
    assert row is not None
    assert row.status == "running"
    assert row.archived_at is None


@pytest.mark.asyncio
async def test_resume_non_archived_returns_false(db_factory, live_session):
    # live_session is 'idle', not 'archived'
    ok = await resume_session(
        db_factory,
        session_id=str(live_session.id),
        guild_id=live_session.guild_id,
        host_id="h",
    )
    assert ok is False


# ---------------------------------------------------------------------------
# restart_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_session_clears_resume_token(db_factory, live_session):
    # Set a resume token first
    async with db_factory() as db:
        async with db.begin():
            row = await db.get(SessionModel, live_session.id)
            row.tool_resume_token = "old-token"

    new_sid = await restart_session(
        db_factory,
        session_id=str(live_session.id),
        guild_id=live_session.guild_id,
        requested_by_id="u",
        deployment_secret_key="x",
    )
    assert new_sid == str(live_session.id)

    async with db_factory() as db:
        row = await db.get(SessionModel, live_session.id)
    assert row is not None
    assert row.tool_resume_token is None
    assert row.status == "running"


@pytest.mark.asyncio
async def test_restart_archived_session_raises(db_factory, live_session):
    await archive_session(
        db_factory,
        session_id=str(live_session.id),
        guild_id=live_session.guild_id,
    )
    with pytest.raises(ValueError, match="not in restartable state"):
        await restart_session(
            db_factory,
            session_id=str(live_session.id),
            guild_id=live_session.guild_id,
            requested_by_id="u",
            deployment_secret_key="x",
        )


# ---------------------------------------------------------------------------
# invite_user_to_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_user_to_session_happy(db_factory, live_session):
    from sqlalchemy import select

    from discode.bot.session_service import invite_user_to_session
    from discode.db.models import SessionMember, User

    ok = await invite_user_to_session(
        db_factory,
        session_id=str(live_session.id),
        guild_id=str(live_session.guild_id),
        invitee_id="invitee-1",
        invitee_username="inv-test",
    )
    assert ok is True
    async with db_factory() as db:
        member = await db.scalar(
            select(SessionMember).where(
                SessionMember.session_id == live_session.id,
                SessionMember.user_id == "invitee-1",
            )
        )
        user = await db.get(User, "invitee-1")
    assert member is not None
    assert member.role == "operator"
    assert user is not None
    assert user.username == "inv-test"


@pytest.mark.asyncio
async def test_invite_user_already_member_idempotent(db_factory, live_session):
    from discode.bot.session_service import invite_user_to_session

    await invite_user_to_session(
        db_factory,
        session_id=str(live_session.id),
        guild_id=str(live_session.guild_id),
        invitee_id="invitee-2",
        invitee_username="inv2",
    )
    ok = await invite_user_to_session(
        db_factory,
        session_id=str(live_session.id),
        guild_id=str(live_session.guild_id),
        invitee_id="invitee-2",
        invitee_username="inv2",
    )
    assert ok is True


@pytest.mark.asyncio
async def test_invite_user_unknown_session_returns_false(db_factory):
    import uuid

    from discode.bot.session_service import invite_user_to_session

    ok = await invite_user_to_session(
        db_factory,
        session_id=str(uuid.uuid4()),
        guild_id="g-nonexistent",
        invitee_id="x",
        invitee_username="x",
    )
    assert ok is False
