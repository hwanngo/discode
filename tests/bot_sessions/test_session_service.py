from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from discode.bot import session_service
from discode.db.engine import make_engine, make_session_factory
from discode.db.models import Guild, RunnerHost, SessionMember, User
from discode.db.models import Session as SessionModel


@pytest_asyncio.fixture
async def db_factory_fixture(migrated_db_url):
    engine = make_engine(migrated_db_url)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


def _unique_ids():
    suffix = uuid.uuid4().hex[:8]
    return f"guild-{suffix}", f"user-{suffix}", f"host-{suffix}"


async def _seed_prerequisites(db_factory, guild_id: str, user_id: str, host_id: str):
    async with db_factory() as db:
        db.add(Guild(id=guild_id, name="Test Guild"))
        db.add(User(id=user_id, username="owner"))
        db.add(RunnerHost(id=host_id, status="online"))
        await db.commit()


async def _seed_session(
    db_factory,
    *,
    guild_id: str,
    owner_id: str,
    host_id: str,
    name: str,
    add_member_user_id: str | None,
) -> str:
    session_id = uuid.uuid4()
    async with db_factory() as db:
        db.add(
            SessionModel(
                id=session_id,
                guild_id=guild_id,
                owner_id=owner_id,
                tool="claude",
                name=name,
                cwd="/tmp/project",
                cwd_realpath="/tmp/project",
                parent_channel_id="parent-1",
                thread_id=f"thread-{uuid.uuid4().hex[:6]}",
                host_id=host_id,
                hook_secret_hash="deadbeef" * 8,
                status="running",
            )
        )
        if add_member_user_id:
            db.add(
                SessionMember(
                    session_id=session_id,
                    user_id=add_member_user_id,
                    role="owner",
                )
            )
        await db.commit()
    return str(session_id)


@pytest.mark.asyncio
async def test_resolve_session_prefers_explicit_uuid(db_factory_fixture):
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)
    session_id = await _seed_session(
        db_factory_fixture,
        guild_id=guild_id,
        owner_id=user_id,
        host_id=host_id,
        name="alpha",
        add_member_user_id=user_id,
    )

    resolved = await session_service.resolve_session_target(
        db_factory_fixture,
        guild_id=guild_id,
        user_id=user_id,
        session_ref=session_id,
    )

    assert resolved is not None
    assert resolved["session_id"] == session_id
    assert resolved["name"] == "alpha"


@pytest.mark.asyncio
async def test_resolve_session_falls_back_to_name_for_member_only(db_factory_fixture):
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    other_user = f"other-{uuid.uuid4().hex[:8]}"
    async with db_factory_fixture() as db:
        db.add(User(id=other_user, username="other"))
        await db.commit()

    member_session_id = await _seed_session(
        db_factory_fixture,
        guild_id=guild_id,
        owner_id=user_id,
        host_id=host_id,
        name="shared-name",
        add_member_user_id=user_id,
    )
    await _seed_session(
        db_factory_fixture,
        guild_id=guild_id,
        owner_id=other_user,
        host_id=host_id,
        name="shared-name",
        add_member_user_id=other_user,
    )

    resolved = await session_service.resolve_session_target(
        db_factory_fixture,
        guild_id=guild_id,
        user_id=user_id,
        session_ref="shared-name",
    )

    assert resolved is not None
    assert resolved["session_id"] == member_session_id


@pytest.mark.asyncio
async def test_resolve_session_uses_latest_session_when_ref_missing(db_factory_fixture):
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)
    await _seed_session(
        db_factory_fixture,
        guild_id=guild_id,
        owner_id=user_id,
        host_id=host_id,
        name="older",
        add_member_user_id=user_id,
    )
    newer_id = await _seed_session(
        db_factory_fixture,
        guild_id=guild_id,
        owner_id=user_id,
        host_id=host_id,
        name="newer",
        add_member_user_id=user_id,
    )

    result = await session_service.resolve_session_target(
        db_factory_fixture,
        guild_id=guild_id,
        user_id=user_id,
        session_ref=None,
    )

    assert result is not None
    assert result["session_id"] == newer_id
    assert result["name"] == "newer"


@pytest.mark.asyncio
async def test_send_input_wrapper_delegates(monkeypatch):
    import discode.bot.sagas.input as _input_saga_mod

    mocked = AsyncMock(return_value="ikey-1")
    monkeypatch.setattr(_input_saga_mod, "run_input_saga", mocked)

    result = await session_service.send_input_for_session(
        AsyncMock(),
        session_id="sid",
        guild_id="gid",
        thread_id="tid",
        host_id="hid",
        owner_id="uid",
        text_content="hello",
    )

    assert result == "ikey-1"
    mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_wrapper_delegates(monkeypatch):
    mocked = AsyncMock(return_value=True)
    monkeypatch.setattr(session_service, "run_stop_saga", mocked)

    result = await session_service.stop_session(
        AsyncMock(), session_id="sid", guild_id="gid", requested_by_id="uid"
    )

    assert result is True
    mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_archive_wrapper_delegates(monkeypatch):
    mocked = AsyncMock(return_value=True)
    monkeypatch.setattr(session_service, "run_archive_saga", mocked)

    result = await session_service.archive_session(AsyncMock(), session_id="sid", guild_id="gid")

    assert result is True
    mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_wrapper_delegates(monkeypatch):
    mocked = AsyncMock(return_value=True)
    monkeypatch.setattr(session_service, "run_resume_saga", mocked)

    result = await session_service.resume_session(
        AsyncMock(), session_id="sid", guild_id="gid", host_id="hid"
    )

    assert result is True
    mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_restart_wrapper_delegates(monkeypatch):
    mocked = AsyncMock(return_value="new-session-id")
    monkeypatch.setattr(session_service, "run_restart_saga", mocked)

    result = await session_service.restart_session(
        AsyncMock(),
        session_id="sid",
        guild_id="gid",
        requested_by_id="uid",
        deployment_secret_key="secret",
    )

    assert result == "new-session-id"
    mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_invite_wrapper_delegates(monkeypatch):
    mocked = AsyncMock(return_value=True)
    monkeypatch.setattr(session_service, "invite_user_to_session", mocked)

    result = await session_service.invite_member(
        AsyncMock(),
        session_id="sid",
        guild_id="gid",
        invitee_id="invitee",
        invitee_username="new-user",
    )

    assert result is True
    mocked.assert_awaited_once()
