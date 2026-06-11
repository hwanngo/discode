from __future__ import annotations

import pytest
import sqlalchemy as sa

from discode.db.models import (
    GuildAdminRolePolicy,
    GuildToolChannelMap,
    RedisOutbox,
    Session,
)


def test_session_status_check_constraint_values() -> None:
    """Session model has 'status' column with correct check constraint values."""
    table = Session.__table__
    status_col = table.c["status"]
    assert status_col is not None

    # Find the status check constraint
    check_texts = []
    for constraint in table.constraints:
        if isinstance(constraint, sa.CheckConstraint):
            check_texts.append(str(constraint.sqltext))

    status_check = next((t for t in check_texts if "creating" in t and "orphaned" in t), None)
    assert status_check is not None, f"Status check constraint not found in: {check_texts}"

    # Verify all expected statuses are in the constraint
    expected_statuses = {
        "creating",
        "running",
        "idle",
        "archived",
        "resuming",
        "stopping",
        "stopped",
        "failed",
        "orphaned",
    }
    for status in expected_statuses:
        assert status in status_check, f"Status '{status}' not found in constraint: {status_check}"


def test_redis_outbox_has_pending_index() -> None:
    """RedisOutbox has pending index."""
    table = RedisOutbox.__table__
    index_names = {idx.name for idx in table.indexes}
    assert "redis_outbox_pending_idx" in index_names


def test_guild_tool_channel_map_columns() -> None:
    """GuildToolChannelMap has all required columns."""
    table = GuildToolChannelMap.__table__
    col_names = {c.name for c in table.columns}
    required = {"id", "guild_id", "tool", "channel_id", "updated_by", "created_at", "updated_at"}
    assert required.issubset(col_names)


def test_guild_tool_channel_map_unique_constraint_defined() -> None:
    """GuildToolChannelMap has a unique constraint on (guild_id, tool)."""
    table = GuildToolChannelMap.__table__
    unique_constraints = [c for c in table.constraints if isinstance(c, sa.UniqueConstraint)]
    col_pairs = [frozenset(c.name for c in uc.columns) for uc in unique_constraints]
    assert frozenset({"guild_id", "tool"}) in col_pairs


def test_guild_admin_role_policy_columns() -> None:
    """GuildAdminRolePolicy has all required columns."""
    table = GuildAdminRolePolicy.__table__
    col_names = {c.name for c in table.columns}
    required = {
        "id",
        "guild_id",
        "role_id",
        "role_name_fallback",
        "updated_by",
        "created_at",
        "updated_at",
    }
    assert required.issubset(col_names)


def test_guild_admin_role_policy_guild_id_unique() -> None:
    """GuildAdminRolePolicy has a unique constraint on guild_id."""
    table = GuildAdminRolePolicy.__table__
    # guild_id column should be unique (either via UniqueConstraint or column-level)
    guild_id_col = table.c["guild_id"]
    unique_constraints = [c for c in table.constraints if isinstance(c, sa.UniqueConstraint)]
    col_sets = [frozenset(c.name for c in uc.columns) for uc in unique_constraints]
    assert frozenset({"guild_id"}) in col_sets or guild_id_col.unique


def test_guild_admin_role_policy_role_id_nullable() -> None:
    """GuildAdminRolePolicy.role_id is nullable."""
    table = GuildAdminRolePolicy.__table__
    role_id_col = table.c["role_id"]
    assert role_id_col.nullable is True


@pytest.mark.asyncio
async def test_guild_tool_channel_map_crud(db_session) -> None:
    """GuildToolChannelMap supports basic insert and select."""
    from sqlalchemy import select

    row = GuildToolChannelMap(
        guild_id="guild-crud-test",
        tool="claude",
        channel_id="123456789",
        updated_by="user-111",
    )
    db_session.add(row)
    await db_session.flush()

    result = await db_session.execute(
        select(GuildToolChannelMap).where(GuildToolChannelMap.guild_id == "guild-crud-test")
    )
    fetched = result.scalar_one()
    assert fetched.channel_id == "123456789"
    assert fetched.tool == "claude"
    assert fetched.created_at is not None


@pytest.mark.asyncio
async def test_guild_admin_role_policy_crud(db_session) -> None:
    """GuildAdminRolePolicy supports basic insert and select."""
    from sqlalchemy import select

    row = GuildAdminRolePolicy(
        guild_id="guild-policy-test",
        role_id=None,
        role_name_fallback=None,
        updated_by="user-222",
    )
    db_session.add(row)
    await db_session.flush()

    result = await db_session.execute(
        select(GuildAdminRolePolicy).where(GuildAdminRolePolicy.guild_id == "guild-policy-test")
    )
    fetched = result.scalar_one()
    assert fetched.role_id is None
    assert fetched.updated_by == "user-222"
    assert fetched.created_at is not None
