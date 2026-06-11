from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select, text

from discode.bot.sagas.create import run_create_saga
from discode.bot.session_service import (
    resolve_session_target,
    send_input_for_session,
    stop_session,
)
from discode.bot.setup_service import (
    add_allowed_root_for_user,
)
from discode.db.engine import make_engine, make_session_factory
from discode.db.models import AllowedRoot, Guild, RedisOutbox, RunnerHost


@pytest_asyncio.fixture
async def db_factory(migrated_db_url: str):  # type: ignore[no-untyped-def]
    engine = make_engine(migrated_db_url)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


async def initialize_guild(
    db_factory,  # type: ignore[no-untyped-def]
    *,
    guild_id: str,
    guild_name: str,
    runner_host_id: str,
) -> None:
    """Seed the Guild + RunnerHost prerequisites for a live-command test.

    Replaces the removed ``setup_service.initialize_guild`` helper; the current
    code seeds these rows inline (see tests/bot_sessions/test_create_saga.py).
    """
    async with db_factory() as db:
        db.add(Guild(id=guild_id, name=guild_name))
        db.add(RunnerHost(id=runner_host_id, status="online"))
        await db.commit()


async def start_session_from_command(
    db_factory,  # type: ignore[no-untyped-def]
    *,
    guild_id: str,
    owner_id: str,
    owner_username: str,
    tool: str,
    name: str,
    cwd: str,
    parent_channel_id: str,
    runner_host_id: str,
    deployment_secret_key: str,
):
    """Shim for the removed ``start_session_from_command``.

    The session-creation flow now lives in ``sagas.create.run_create_saga``,
    which requires an explicit ``cwd_realpath``.
    """
    return await run_create_saga(
        db_factory,
        guild_id=guild_id,
        owner_id=owner_id,
        owner_username=owner_username,
        tool=tool,
        name=name,
        cwd=cwd,
        cwd_realpath=cwd,
        parent_channel_id=parent_channel_id,
        runner_host_id=runner_host_id,
        deployment_secret_key=deployment_secret_key,
    )


@pytest.mark.asyncio
async def test_initialize_guild_upserts_guild_and_runner_host(db_factory) -> None:  # type: ignore[no-untyped-def]
    guild_id = f"guild-{uuid.uuid4().hex[:8]}"
    host_id = f"runner-{uuid.uuid4().hex[:8]}"

    await initialize_guild(
        db_factory,
        guild_id=guild_id,
        guild_name="Guild",
        runner_host_id=host_id,
    )

    async with db_factory() as db:
        assert await db.get(Guild, guild_id) is not None
        assert await db.get(RunnerHost, host_id) is not None


@pytest.mark.asyncio
async def test_add_allowed_root_for_user_persists_root(db_factory, tmp_path) -> None:  # type: ignore[no-untyped-def]
    guild_id = f"guild-{uuid.uuid4().hex[:8]}"
    host_id = f"runner-{uuid.uuid4().hex[:8]}"
    await initialize_guild(
        db_factory,
        guild_id=guild_id,
        guild_name="Guild",
        runner_host_id=host_id,
    )

    root = await add_allowed_root_for_user(
        db_factory,
        guild_id=guild_id,
        user_id="user-1",
        runner_host_id=host_id,
        path=str(tmp_path),
        created_by="user-1",
    )

    async with db_factory() as db:
        rows = (await db.execute(select(AllowedRoot))).scalars().all()
    assert len(rows) == 1
    assert str(rows[0].id) == root.id
    assert rows[0].realpath == str(tmp_path.resolve())


@pytest.mark.asyncio
async def test_start_session_from_command_writes_runner_outbox(db_factory, tmp_path) -> None:  # type: ignore[no-untyped-def]
    guild_id = f"guild-{uuid.uuid4().hex[:8]}"
    host_id = f"runner-{uuid.uuid4().hex[:8]}"
    key = Fernet.generate_key().decode()
    await initialize_guild(
        db_factory,
        guild_id=guild_id,
        guild_name="Guild",
        runner_host_id=host_id,
    )
    await add_allowed_root_for_user(
        db_factory,
        guild_id=guild_id,
        user_id="user-1",
        runner_host_id=host_id,
        path=str(tmp_path),
        created_by="user-1",
    )

    result = await start_session_from_command(
        db_factory,
        guild_id=guild_id,
        owner_id="user-1",
        owner_username="alice",
        tool="codex",
        name="demo",
        cwd=str(tmp_path),
        parent_channel_id="channel-1",
        runner_host_id=host_id,
        deployment_secret_key=key,
    )

    async with db_factory() as db:
        outbox = (
            (
                await db.execute(
                    select(RedisOutbox).where(
                        RedisOutbox.envelope_type == "runner.create_session.v1",
                    )
                )
            )
            .scalars()
            .all()
        )
    outbox = [row for row in outbox if row.payload.get("session_id") == result.session_id]
    assert result.session_id
    assert len(outbox) == 1


@pytest.mark.asyncio
async def test_send_path_writes_runner_send_input_outbox_for_target_session(
    db_factory, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    guild_id = f"guild-{uuid.uuid4().hex[:8]}"
    host_id = f"runner-{uuid.uuid4().hex[:8]}"
    key = Fernet.generate_key().decode()
    await initialize_guild(
        db_factory,
        guild_id=guild_id,
        guild_name="Guild",
        runner_host_id=host_id,
    )
    await add_allowed_root_for_user(
        db_factory,
        guild_id=guild_id,
        user_id="user-1",
        runner_host_id=host_id,
        path=str(tmp_path),
        created_by="user-1",
    )

    alpha_cwd = tmp_path / "alpha"
    beta_cwd = tmp_path / "beta"
    alpha_cwd.mkdir()
    beta_cwd.mkdir()

    await start_session_from_command(
        db_factory,
        guild_id=guild_id,
        owner_id="user-1",
        owner_username="alice",
        tool="codex",
        name="alpha",
        cwd=str(alpha_cwd),
        parent_channel_id="channel-1",
        runner_host_id=host_id,
        deployment_secret_key=key,
    )
    beta = await start_session_from_command(
        db_factory,
        guild_id=guild_id,
        owner_id="user-1",
        owner_username="alice",
        tool="codex",
        name="beta",
        cwd=str(beta_cwd),
        parent_channel_id="channel-1",
        runner_host_id=host_id,
        deployment_secret_key=key,
    )

    target = await resolve_session_target(
        db_factory,
        guild_id=guild_id,
        user_id="user-1",
        session_ref="beta",
    )
    assert target is not None

    input_key = await send_input_for_session(
        db_factory,
        session_id=str(target["session_id"]),
        guild_id=guild_id,
        thread_id=str(target["thread_id"]),
        host_id=str(target["host_id"]),
        owner_id=str(target["owner_id"]),
        text_content="hello from send path",
    )

    async with db_factory() as db:
        outbox = (
            (
                await db.execute(
                    select(RedisOutbox).where(
                        RedisOutbox.envelope_type == "runner.send_input.v1",
                    )
                )
            )
            .scalars()
            .all()
        )

    matching = [row for row in outbox if row.payload.get("session_id") == beta.session_id]
    assert input_key
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_stop_path_writes_runner_stop_session_outbox_for_target_session(
    db_factory, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    guild_id = f"guild-{uuid.uuid4().hex[:8]}"
    host_id = f"runner-{uuid.uuid4().hex[:8]}"
    key = Fernet.generate_key().decode()
    await initialize_guild(
        db_factory,
        guild_id=guild_id,
        guild_name="Guild",
        runner_host_id=host_id,
    )
    await add_allowed_root_for_user(
        db_factory,
        guild_id=guild_id,
        user_id="user-1",
        runner_host_id=host_id,
        path=str(tmp_path),
        created_by="user-1",
    )

    alpha_cwd = tmp_path / "alpha"
    beta_cwd = tmp_path / "beta"
    alpha_cwd.mkdir()
    beta_cwd.mkdir()

    await start_session_from_command(
        db_factory,
        guild_id=guild_id,
        owner_id="user-1",
        owner_username="alice",
        tool="codex",
        name="alpha",
        cwd=str(alpha_cwd),
        parent_channel_id="channel-1",
        runner_host_id=host_id,
        deployment_secret_key=key,
    )
    beta = await start_session_from_command(
        db_factory,
        guild_id=guild_id,
        owner_id="user-1",
        owner_username="alice",
        tool="codex",
        name="beta",
        cwd=str(beta_cwd),
        parent_channel_id="channel-1",
        runner_host_id=host_id,
        deployment_secret_key=key,
    )

    target = await resolve_session_target(
        db_factory,
        guild_id=guild_id,
        user_id="user-1",
        session_ref="beta",
    )
    assert target is not None

    async with db_factory() as db:
        await db.execute(
            text("UPDATE sessions SET status = 'resuming' WHERE id = :sid"),
            {"sid": beta.session_id},
        )
        await db.commit()

    stopped = await stop_session(
        db_factory,
        session_id=str(target["session_id"]),
        guild_id=guild_id,
        requested_by_id="user-1",
    )

    async with db_factory() as db:
        outbox = (
            (
                await db.execute(
                    select(RedisOutbox).where(
                        RedisOutbox.envelope_type == "runner.stop_session.v1",
                    )
                )
            )
            .scalars()
            .all()
        )

    matching = [row for row in outbox if row.payload.get("session_id") == beta.session_id]
    assert stopped is True
    assert len(matching) == 1
