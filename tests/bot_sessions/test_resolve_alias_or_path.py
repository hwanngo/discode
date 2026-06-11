from __future__ import annotations

import uuid

import pytest

from discode.db.models import Guild


def _unique_ids() -> tuple[str, str]:
    return f"g-{uuid.uuid4().hex[:8]}", f"h-{uuid.uuid4().hex[:8]}"


async def _seed_guild(db_factory_fixture, guild_id: str) -> None:
    async with db_factory_fixture() as db:
        db.add(Guild(id=guild_id, name=guild_id))
        await db.commit()


@pytest.mark.asyncio
async def test_resolve_absolute_path_passes_through(db_factory_fixture) -> None:
    from discode.bot.setup_service import resolve_alias_or_path

    guild_id, _ = _unique_ids()
    await _seed_guild(db_factory_fixture, guild_id)

    out = await resolve_alias_or_path(
        db_factory_fixture,
        guild_id=guild_id,
        raw_cwd="/tmp/anywhere",
    )
    assert out == "/tmp/anywhere"


@pytest.mark.asyncio
async def test_resolve_known_alias_returns_realpath(db_factory_fixture) -> None:
    from discode.bot.setup_service import resolve_alias_or_path, set_path_alias

    guild_id, runner_host_id = _unique_ids()
    await _seed_guild(db_factory_fixture, guild_id)
    await set_path_alias(
        db_factory_fixture,
        guild_id=guild_id,
        alias="myrepo",
        path="/Users/admin/Personal/discode",
        runner_host_id=runner_host_id,
        created_by="u",
    )

    out = await resolve_alias_or_path(
        db_factory_fixture,
        guild_id=guild_id,
        raw_cwd="myrepo",
    )
    assert out == "/Users/admin/Personal/discode"


@pytest.mark.asyncio
async def test_resolve_unknown_alias_raises_value_error(db_factory_fixture) -> None:
    from discode.bot.setup_service import resolve_alias_or_path

    guild_id, _ = _unique_ids()
    await _seed_guild(db_factory_fixture, guild_id)

    with pytest.raises(ValueError, match="unknown_alias"):
        await resolve_alias_or_path(
            db_factory_fixture,
            guild_id=guild_id,
            raw_cwd="nope",
        )


@pytest.mark.asyncio
async def test_resolve_alias_scoped_to_guild(db_factory_fixture) -> None:
    from discode.bot.setup_service import resolve_alias_or_path, set_path_alias

    guild_a, host = _unique_ids()
    guild_b = f"g-{uuid.uuid4().hex[:8]}"
    await _seed_guild(db_factory_fixture, guild_a)
    await _seed_guild(db_factory_fixture, guild_b)

    result = await set_path_alias(
        db_factory_fixture,
        guild_id=guild_a,
        alias="r",
        path="/tmp/a",
        runner_host_id=host,
        created_by="u",
    )

    assert (
        await resolve_alias_or_path(
            db_factory_fixture,
            guild_id=guild_a,
            raw_cwd="r",
        )
        == result.realpath
    )

    with pytest.raises(ValueError, match="unknown_alias"):
        await resolve_alias_or_path(
            db_factory_fixture,
            guild_id=guild_b,
            raw_cwd="r",
        )
