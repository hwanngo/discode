from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy import text

from discode.bot.sagas.create import run_create_saga
from discode.db.engine import make_engine, make_session_factory
from discode.db.models import Guild, RunnerHost, User
from discode.db.models import Session as SessionModel

TEST_DEPLOYMENT_KEY = Fernet.generate_key().decode()


@pytest_asyncio.fixture
async def db_factory_fixture(migrated_db_url):
    engine = make_engine(migrated_db_url)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


def _unique_ids():
    return (
        f"guild-{uuid.uuid4().hex[:8]}",
        f"user-{uuid.uuid4().hex[:8]}",
        f"host-{uuid.uuid4().hex[:8]}",
    )


async def _seed_prerequisites(db_factory, guild_id, user_id, host_id):
    async with db_factory() as db:
        db.add(Guild(id=guild_id, name="Test Guild"))
        db.add(User(id=user_id, username="testuser"))
        db.add(RunnerHost(id=host_id, status="online"))
        await db.commit()


def _make_saga_kwargs(guild_id, user_id, host_id, **overrides):
    base = dict(
        guild_id=guild_id,
        owner_id=user_id,
        owner_username="testuser",
        tool="claude",
        name="my-session",
        cwd="/home/user/project",
        cwd_realpath="/home/user/project",
        parent_channel_id="channel-001",
        runner_host_id=host_id,
        deployment_secret_key=TEST_DEPLOYMENT_KEY,
    )
    base.update(overrides)
    return base


async def _seed_archived_session(db_factory, guild_id, user_id, host_id, name, resume_token=None):
    """Insert an archived session and return its UUID string."""
    session_id = uuid.uuid4()
    async with db_factory() as db:
        db.add(
            SessionModel(
                id=session_id,
                guild_id=guild_id,
                owner_id=user_id,
                tool="claude",
                name=name,
                cwd="/home/user/project",
                cwd_realpath="/home/user/project",
                parent_channel_id="channel-001",
                thread_id=None,
                host_id=host_id,
                hook_secret_hash="deadbeef" * 8,
                status="creating",
            )
        )
        await db.commit()

    # Transition to archived + resume_token
    async with db_factory() as db:
        async with db.begin():
            await db.execute(
                text("UPDATE sessions SET status='archived', tool_resume_token=:tok WHERE id=:sid"),
                {"sid": str(session_id), "tok": resume_token},
            )
    return str(session_id)


@pytest.mark.asyncio
async def test_resume_by_name_copies_resume_token(db_factory_fixture):
    """run_create_saga with a name matching an archived session uses its resume_token."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    archived_id = await _seed_archived_session(
        db_factory_fixture,
        guild_id,
        user_id,
        host_id,
        name="my-session",
        resume_token="test-resume-token",
    )

    result = await run_create_saga(
        db_factory_fixture,
        **_make_saga_kwargs(guild_id, user_id, host_id, name="my-session"),
    )

    assert result.resumed_from_session_id == archived_id

    # Outbox payload should carry the resume_token from the archived session
    from discode.db.models import RedisOutbox

    async with db_factory_fixture() as db:
        outbox_result = await db.execute(
            sa.select(RedisOutbox).where(
                sa.func.json_extract_path_text(RedisOutbox.payload, "session_id")
                == result.session_id
            )
        )
        outbox_row = outbox_result.scalar_one()
    assert outbox_row.payload["resume_token"] == "test-resume-token"


@pytest.mark.asyncio
async def test_resume_by_name_marks_old_session_superseded(db_factory_fixture):
    """run_create_saga marks the archived session's status_reason as 'superseded'."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    archived_id = await _seed_archived_session(
        db_factory_fixture,
        guild_id,
        user_id,
        host_id,
        name="my-session",
        resume_token="tok-123",
    )

    await run_create_saga(
        db_factory_fixture,
        **_make_saga_kwargs(guild_id, user_id, host_id, name="my-session"),
    )

    async with db_factory_fixture() as db:
        row = await db.execute(
            text("SELECT status_reason FROM sessions WHERE id=:sid"),
            {"sid": archived_id},
        )
        status_reason = row.scalar_one()
    assert status_reason == "superseded"


@pytest.mark.asyncio
async def test_resume_by_name_stopped_creates_fresh(db_factory_fixture):
    """run_create_saga with a name matching a STOPPED (not archived) session creates fresh."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    # Seed a stopped session (not archived)
    session_id = uuid.uuid4()
    async with db_factory_fixture() as db:
        db.add(
            SessionModel(
                id=session_id,
                guild_id=guild_id,
                owner_id=user_id,
                tool="claude",
                name="my-session",
                cwd="/home/user/project",
                cwd_realpath="/home/user/project",
                parent_channel_id="channel-001",
                thread_id=None,
                host_id=host_id,
                hook_secret_hash="deadbeef" * 8,
                status="creating",
            )
        )
        await db.commit()

    async with db_factory_fixture() as db:
        async with db.begin():
            await db.execute(
                text("UPDATE sessions SET status='stopped' WHERE id=:sid"),
                {"sid": str(session_id)},
            )

    result = await run_create_saga(
        db_factory_fixture,
        **_make_saga_kwargs(guild_id, user_id, host_id, name="my-session"),
    )

    # Should be a fresh session with no resume linkage
    assert result.session_id != str(session_id)
    assert result.resumed_from_session_id is None


@pytest.mark.asyncio
async def test_new_name_creates_fresh_session(db_factory_fixture):
    """run_create_saga with a new name creates a fresh session (no lookup)."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    result = await run_create_saga(
        db_factory_fixture,
        **_make_saga_kwargs(guild_id, user_id, host_id, name="brand-new-session"),
    )

    assert result.resumed_from_session_id is None

    # Outbox resume_token should be None
    from discode.db.models import RedisOutbox

    async with db_factory_fixture() as db:
        outbox_result = await db.execute(
            sa.select(RedisOutbox).where(
                sa.func.json_extract_path_text(RedisOutbox.payload, "session_id")
                == result.session_id
            )
        )
        outbox_row = outbox_result.scalar_one()
    assert outbox_row.payload["resume_token"] is None
