from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from discode.db.models import AllowedRoot, Guild, PathAlias


def _unique_ids() -> tuple[str, str]:
    return f"g-{uuid.uuid4().hex[:8]}", f"h-{uuid.uuid4().hex[:8]}"


async def _seed_guild(db_factory_fixture, guild_id: str) -> None:
    async with db_factory_fixture() as db:
        db.add(Guild(id=guild_id, name=guild_id))
        await db.commit()


@pytest.mark.asyncio
async def test_set_path_alias_canonicalizes_and_inserts(db_factory_fixture) -> None:
    from discode.bot.setup_service import set_path_alias

    guild_id, runner_host_id = _unique_ids()
    await _seed_guild(db_factory_fixture, guild_id)

    result = await set_path_alias(
        db_factory_fixture,
        guild_id=guild_id,
        alias="discode",
        path="/Users/admin/Personal/discode",
        runner_host_id=runner_host_id,
        created_by="actor-1",
    )
    assert result.alias == "discode"
    assert result.realpath == "/Users/admin/Personal/discode"

    async with db_factory_fixture() as db:
        rows = list(
            (await db.execute(select(PathAlias).where(PathAlias.guild_id == guild_id)))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].path == "/Users/admin/Personal/discode"


@pytest.mark.asyncio
async def test_set_path_alias_upserts_allowed_root_with_empty_user(
    db_factory_fixture,
) -> None:
    from discode.bot.setup_service import set_path_alias

    guild_id, runner_host_id = _unique_ids()
    await _seed_guild(db_factory_fixture, guild_id)

    await set_path_alias(
        db_factory_fixture,
        guild_id=guild_id,
        alias="repo",
        path="/Users/admin/Personal/discode",
        runner_host_id=runner_host_id,
        created_by="actor-1",
    )

    async with db_factory_fixture() as db:
        rows = list(
            (
                await db.execute(
                    select(AllowedRoot).where(
                        AllowedRoot.guild_id == guild_id,
                        AllowedRoot.runner_host_id == runner_host_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].user_id == ""
    assert rows[0].realpath == "/Users/admin/Personal/discode"


@pytest.mark.asyncio
async def test_set_path_alias_replaces_existing(db_factory_fixture) -> None:
    from discode.bot.setup_service import set_path_alias

    guild_id, runner_host_id = _unique_ids()
    await _seed_guild(db_factory_fixture, guild_id)

    await set_path_alias(
        db_factory_fixture,
        guild_id=guild_id,
        alias="r",
        path="/path/a",
        runner_host_id=runner_host_id,
        created_by="u",
    )
    second = await set_path_alias(
        db_factory_fixture,
        guild_id=guild_id,
        alias="r",
        path="/path/b",
        runner_host_id=runner_host_id,
        created_by="u",
    )
    assert second.realpath == "/path/b"

    async with db_factory_fixture() as db:
        rows = list(
            (await db.execute(select(PathAlias).where(PathAlias.guild_id == guild_id)))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].realpath == "/path/b"


@pytest.mark.asyncio
async def test_set_path_alias_rejects_denylisted_path(db_factory_fixture) -> None:
    from discode.bot.setup_service import set_path_alias

    guild_id, runner_host_id = _unique_ids()
    await _seed_guild(db_factory_fixture, guild_id)

    with pytest.raises(ValueError):
        await set_path_alias(
            db_factory_fixture,
            guild_id=guild_id,
            alias="bad",
            path="/etc",
            runner_host_id=runner_host_id,
            created_by="u",
        )


@pytest.mark.asyncio
async def test_set_path_alias_rejects_invalid_alias_format(db_factory_fixture) -> None:
    from discode.bot.setup_service import set_path_alias

    guild_id, runner_host_id = _unique_ids()
    await _seed_guild(db_factory_fixture, guild_id)

    with pytest.raises(ValueError):
        await set_path_alias(
            db_factory_fixture,
            guild_id=guild_id,
            alias="UPPER",
            path="/tmp",
            runner_host_id=runner_host_id,
            created_by="u",
        )


@pytest.mark.asyncio
async def test_list_path_aliases_returns_all_for_guild_alphabetical(
    db_factory_fixture,
) -> None:
    from discode.bot.setup_service import list_path_aliases, set_path_alias

    guild_id, runner_host_id = _unique_ids()
    await _seed_guild(db_factory_fixture, guild_id)

    for alias, path in [("zeta", "/tmp/z"), ("alpha", "/tmp/a"), ("mid", "/tmp/m")]:
        await set_path_alias(
            db_factory_fixture,
            guild_id=guild_id,
            alias=alias,
            path=path,
            runner_host_id=runner_host_id,
            created_by="u",
        )

    rows = await list_path_aliases(db_factory_fixture, guild_id=guild_id)
    assert [r.alias for r in rows] == ["alpha", "mid", "zeta"]


@pytest.mark.asyncio
async def test_list_path_aliases_empty_for_unknown_guild(db_factory_fixture) -> None:
    from discode.bot.setup_service import list_path_aliases

    rows = await list_path_aliases(db_factory_fixture, guild_id="g-nonexistent")
    assert rows == []


@pytest.mark.asyncio
async def test_remove_path_alias_returns_true_when_deleted(db_factory_fixture) -> None:
    from discode.bot.setup_service import remove_path_alias, set_path_alias

    guild_id, runner_host_id = _unique_ids()
    await _seed_guild(db_factory_fixture, guild_id)

    await set_path_alias(
        db_factory_fixture,
        guild_id=guild_id,
        alias="r",
        path="/tmp",
        runner_host_id=runner_host_id,
        created_by="u",
    )
    deleted = await remove_path_alias(
        db_factory_fixture,
        guild_id=guild_id,
        alias="r",
    )
    assert deleted is True


@pytest.mark.asyncio
async def test_remove_path_alias_returns_false_when_missing(db_factory_fixture) -> None:
    from discode.bot.setup_service import remove_path_alias

    guild_id, _ = _unique_ids()
    await _seed_guild(db_factory_fixture, guild_id)

    deleted = await remove_path_alias(
        db_factory_fixture,
        guild_id=guild_id,
        alias="nope",
    )
    assert deleted is False
