from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa
from cryptography.fernet import Fernet

from discode.bot.sagas.create import run_create_saga
from discode.db.engine import make_engine, make_session_factory
from discode.db.models import (
    Guild,
    RedisOutbox,
    RunnerHost,
    SessionMember,
    User,
)
from discode.db.models import (
    Session as SessionModel,
)

TEST_DEPLOYMENT_KEY = Fernet.generate_key().decode()


@pytest_asyncio.fixture
async def db_factory_fixture(migrated_db_url):
    engine = make_engine(migrated_db_url)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


def _unique_ids():
    """Return unique guild_id, user_id, host_id for test isolation."""
    return (
        f"guild-{uuid.uuid4().hex[:8]}",
        f"user-{uuid.uuid4().hex[:8]}",
        f"host-{uuid.uuid4().hex[:8]}",
    )


async def _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id):
    """Insert Guild, User, RunnerHost rows needed by the saga."""
    async with db_factory_fixture() as db:
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


@pytest.mark.asyncio
async def test_create_saga_inserts_all_rows(db_factory_fixture):
    """run_create_saga inserts Session, SessionMember, and RedisOutbox all in one tx."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    result = await run_create_saga(
        db_factory_fixture, **_make_saga_kwargs(guild_id, user_id, host_id)
    )

    session_uuid = uuid.UUID(result.session_id)

    async with db_factory_fixture() as db:
        session_row = await db.get(SessionModel, session_uuid)
        assert session_row is not None
        assert session_row.status == "creating"

        member = await db.get(SessionMember, {"session_id": session_uuid, "user_id": user_id})
        assert member is not None
        assert member.role == "owner"

        outbox_result = await db.execute(
            sa.select(RedisOutbox).where(
                sa.func.json_extract_path_text(RedisOutbox.payload, "session_id")
                == result.session_id
            )
        )
        outbox_row = outbox_result.scalar_one_or_none()
        assert outbox_row is not None


@pytest.mark.asyncio
async def test_create_saga_result_fields(db_factory_fixture):
    """CreateSessionResult has valid session_id (UUID)."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    result = await run_create_saga(
        db_factory_fixture, **_make_saga_kwargs(guild_id, user_id, host_id)
    )

    # session_id is parseable UUID
    parsed = uuid.UUID(result.session_id)
    assert str(parsed) == result.session_id


@pytest.mark.asyncio
async def test_hook_secret_ciphertext_decryptable(db_factory_fixture):
    """hook_secret_ciphertext is non-empty and decryptable to 32 bytes."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    result = await run_create_saga(
        db_factory_fixture, **_make_saga_kwargs(guild_id, user_id, host_id)
    )

    assert result.hook_secret_ciphertext
    fernet = Fernet(TEST_DEPLOYMENT_KEY.encode())
    decrypted = fernet.decrypt(result.hook_secret_ciphertext.encode())
    assert len(decrypted) == 32


@pytest.mark.asyncio
async def test_multiple_sessions_same_cwd(db_factory_fixture):
    """Multiple sessions in the same cwd are supported — different tools or
    different names — because each user message spawns its own short-lived
    subprocess (no shared per-cwd state to collide on)."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    base = _make_saga_kwargs(guild_id, user_id, host_id, cwd_realpath="/home/user/shared")
    first = await run_create_saga(db_factory_fixture, **base)

    # Different name and tool, same cwd
    second_kwargs = {**base, "tool": "codex", "name": "second-session"}
    second = await run_create_saga(db_factory_fixture, **second_kwargs)
    assert second.session_id != first.session_id

    # Same tool, different name, same cwd
    third_kwargs = {**base, "name": "third-session"}
    third = await run_create_saga(db_factory_fixture, **third_kwargs)
    assert third.session_id not in {first.session_id, second.session_id}


@pytest.mark.asyncio
async def test_outbox_row_envelope_type_and_session_id(db_factory_fixture):
    """Outbox row has envelope_type='runner.create_session.v1' and correct session_id in payload."""
    guild_id, user_id, host_id = _unique_ids()
    await _seed_prerequisites(db_factory_fixture, guild_id, user_id, host_id)

    result = await run_create_saga(
        db_factory_fixture, **_make_saga_kwargs(guild_id, user_id, host_id)
    )

    async with db_factory_fixture() as db:
        outbox_result = await db.execute(
            sa.select(RedisOutbox).where(
                sa.func.json_extract_path_text(RedisOutbox.payload, "session_id")
                == result.session_id
            )
        )
        outbox_row = outbox_result.scalar_one()
        assert outbox_row.envelope_type == "runner.create_session.v1"
        assert outbox_row.payload["session_id"] == result.session_id
