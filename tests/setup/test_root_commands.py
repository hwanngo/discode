from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from discode.bot.commands.root import add_root, list_roots, remove_root
from discode.db.engine import make_engine, make_session_factory
from discode.db.models import Guild, RunnerHost


@pytest_asyncio.fixture
async def seeded_guild(db_session: AsyncSession) -> Guild:
    guild = Guild(id=f"rc-guild-{uuid.uuid4().hex[:8]}", name="Roots Guild")
    db_session.add(guild)
    await db_session.commit()
    return guild


@pytest_asyncio.fixture
async def seeded_runner_host(db_session: AsyncSession) -> RunnerHost:
    host = RunnerHost(id=f"runner-{uuid.uuid4().hex[:8]}", status="online")
    db_session.add(host)
    await db_session.commit()
    return host


@pytest_asyncio.fixture
async def db_factory(migrated_db_url: str):  # type: ignore[no-untyped-def]
    engine = make_engine(migrated_db_url)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_add_root_inserts_row(
    db_session: AsyncSession,
    db_factory: object,
    seeded_guild: Guild,
    seeded_runner_host: RunnerHost,
) -> None:
    root = await add_root(
        db_factory,  # type: ignore[arg-type]
        guild_id=seeded_guild.id,
        user_id="user-1",
        runner_host_id=seeded_runner_host.id,
        path="/home/user/project",
        realpath="/home/user/project",
        created_by="user-1",
    )
    assert root.id is not None
    assert root.realpath == "/home/user/project"
    assert root.guild_id == seeded_guild.id


@pytest.mark.asyncio
async def test_add_root_duplicate_raises(
    db_session: AsyncSession,
    db_factory: object,
    seeded_guild: Guild,
    seeded_runner_host: RunnerHost,
) -> None:
    realpath = f"/home/user/dup-{uuid.uuid4().hex[:6]}"
    kwargs: dict[str, object] = dict(
        db_factory=db_factory,
        guild_id=seeded_guild.id,
        user_id="user-2",
        runner_host_id=seeded_runner_host.id,
        path=realpath,
        realpath=realpath,
        created_by="user-2",
    )
    await add_root(**kwargs)  # type: ignore[arg-type]
    with pytest.raises((ValueError, Exception)):
        await add_root(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_remove_root_returns_true(
    db_session: AsyncSession,
    db_factory: object,
    seeded_guild: Guild,
    seeded_runner_host: RunnerHost,
) -> None:
    realpath = f"/home/user/toremove-{uuid.uuid4().hex[:6]}"
    await add_root(
        db_factory,  # type: ignore[arg-type]
        guild_id=seeded_guild.id,
        user_id="user-3",
        runner_host_id=seeded_runner_host.id,
        path=realpath,
        realpath=realpath,
        created_by="user-3",
    )
    deleted = await remove_root(
        db_factory,  # type: ignore[arg-type]
        guild_id=seeded_guild.id,
        runner_host_id=seeded_runner_host.id,
        realpath=realpath,
    )
    assert deleted is True


@pytest.mark.asyncio
async def test_remove_root_nonexistent_returns_false(
    db_factory: object,
    seeded_guild: Guild,
    seeded_runner_host: RunnerHost,
) -> None:
    deleted = await remove_root(
        db_factory,  # type: ignore[arg-type]
        guild_id=seeded_guild.id,
        runner_host_id=seeded_runner_host.id,
        realpath="/nonexistent/path",
    )
    assert deleted is False


@pytest.mark.asyncio
async def test_list_roots_returns_all(
    db_session: AsyncSession,
    db_factory: object,
    seeded_guild: Guild,
    seeded_runner_host: RunnerHost,
) -> None:
    realpaths = [f"/home/user/proj-{uuid.uuid4().hex[:6]}" for _ in range(3)]
    for i, rp in enumerate(realpaths):
        await add_root(
            db_factory,  # type: ignore[arg-type]
            guild_id=seeded_guild.id,
            user_id=f"user-list-{i}",
            runner_host_id=seeded_runner_host.id,
            path=rp,
            realpath=rp,
            created_by=f"user-list-{i}",
        )
    roots = await list_roots(db_factory, guild_id=seeded_guild.id)  # type: ignore[arg-type]
    found = [r.realpath for r in roots]
    for rp in realpaths:
        assert rp in found
