from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from discode.db.models import Guild, PathAlias


def _gid() -> str:
    return f"g-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_path_alias_can_be_inserted_and_read_back(db_session) -> None:
    guild_id = _gid()
    db_session.add(Guild(id=guild_id, name=guild_id))
    db_session.add(
        PathAlias(
            guild_id=guild_id,
            alias="discode",
            path="/Users/admin/Personal/discode",
            realpath="/Users/admin/Personal/discode",
            created_by="u-1",
        )
    )
    await db_session.flush()

    result = await db_session.execute(select(PathAlias).where(PathAlias.guild_id == guild_id))
    rows = list(result.scalars().all())
    assert len(rows) == 1
    assert rows[0].alias == "discode"
    assert rows[0].realpath == "/Users/admin/Personal/discode"


@pytest.mark.asyncio
async def test_path_alias_uq_constraint(db_session) -> None:
    """(guild_id, alias) uniqueness — second insert with same alias raises."""
    guild_id = _gid()
    db_session.add(Guild(id=guild_id, name=guild_id))
    db_session.add(
        PathAlias(
            guild_id=guild_id,
            alias="repo",
            path="/path/a",
            realpath="/path/a",
            created_by="u",
        )
    )
    await db_session.flush()

    db_session.add(
        PathAlias(
            guild_id=guild_id,
            alias="repo",
            path="/path/b",
            realpath="/path/b",
            created_by="u",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["UPPER", "1starts-digit", "has spaces", "has/slash", ""])
async def test_path_alias_alias_format_check(db_session, bad) -> None:
    """Bad alias formats are rejected by the DB CHECK constraint."""
    guild_id = _gid()
    db_session.add(Guild(id=guild_id, name=guild_id))
    db_session.add(
        PathAlias(
            guild_id=guild_id,
            alias=bad,
            path="/p",
            realpath="/p",
            created_by="u",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_path_alias_realpath_check(db_session) -> None:
    """realpath must start with /."""
    guild_id = _gid()
    db_session.add(Guild(id=guild_id, name=guild_id))
    db_session.add(
        PathAlias(
            guild_id=guild_id,
            alias="bad",
            path="relative",
            realpath="not-absolute",
            created_by="u",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
