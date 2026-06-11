"""Tests for the /tool service layer helpers."""

from __future__ import annotations

import dataclasses
import uuid

import pytest
import pytest_asyncio

from discode.bot.tool_service import (
    disable_tool,
    enable_tool,
    list_tools,
    register_tool,
    remove_tool,
    show_tool,
    update_tool_field,
)
from discode.db.models import Guild, GuildAdminRolePolicy

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

ADMIN_ROLE_ID = "999"
ADMIN_ROLE_NAME = "admin"
NON_ADMIN_ROLE_NAMES = ["member"]
NON_ADMIN_ROLE_IDS: list[str] = []


@dataclasses.dataclass
class GuildAdminCtx:
    guild_id: str
    admin_role_ids: list[str]
    admin_role_names: list[str]


@pytest_asyncio.fixture
async def guild_with_admin_role(db_session):
    """Seed a guild + admin-role policy; return a context dataclass."""
    guild_id = f"guild-{uuid.uuid4().hex[:8]}"
    db_session.add(Guild(id=guild_id, name="Test Guild"))
    db_session.add(
        GuildAdminRolePolicy(
            guild_id=guild_id,
            role_id=ADMIN_ROLE_ID,
            role_name_fallback=ADMIN_ROLE_NAME,
            updated_by="test",
        )
    )
    await db_session.flush()
    return GuildAdminCtx(
        guild_id=guild_id,
        admin_role_ids=[ADMIN_ROLE_ID],
        admin_role_names=[ADMIN_ROLE_NAME],
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _valid_cfg(**overrides):
    base = dict(
        display_name="Test Tool",
        argv_prefix=["mytool"],
        argv_resume_tokens=[],
        argv_suffix=["{prompt}"],
        uses_pty=False,
        resume_token_source="none",
        resume_token_pattern=None,
        reply_extractor="stdout_raw",
        reply_json_field=None,
        extra_paths=[],
        env_passthrough=[],
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# register_tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_tool_happy(db_session, guild_with_admin_role):
    result = await register_tool(
        db_session,
        guild_id=guild_with_admin_role.guild_id,
        name="myaider",
        config=_valid_cfg(),
        created_by="user1",
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    await db_session.commit()
    assert result.authorized is True
    assert result.success is True


@pytest.mark.asyncio
async def test_register_tool_unauthorized(db_session, guild_with_admin_role):
    result = await register_tool(
        db_session,
        guild_id=guild_with_admin_role.guild_id,
        name="x",
        config=_valid_cfg(),
        created_by="u",
        member_roles=[],
        member_role_names=NON_ADMIN_ROLE_NAMES,
    )
    assert result.authorized is False


@pytest.mark.asyncio
async def test_register_tool_rejects_bad_name(db_session, guild_with_admin_role):
    result = await register_tool(
        db_session,
        guild_id=guild_with_admin_role.guild_id,
        name="UPPER",
        config=_valid_cfg(),
        created_by="u",
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    assert result.success is False
    assert "name" in result.message.lower()


@pytest.mark.asyncio
async def test_register_tool_rejects_argv_suffix_without_prompt(db_session, guild_with_admin_role):
    result = await register_tool(
        db_session,
        guild_id=guild_with_admin_role.guild_id,
        name="ok",
        config=_valid_cfg(argv_suffix=["nope"]),
        created_by="u",
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    assert result.success is False
    assert "{prompt}" in result.message


# ---------------------------------------------------------------------------
# list_tools / show_tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_open_to_all(db_session):
    result = await list_tools(db_session)
    names = [r.name for r in result.tools]
    assert "claude" in names
    assert "pi" in names  # disabled rows are still listed


@pytest.mark.asyncio
async def test_show_tool_returns_row(db_session):
    result = await show_tool(db_session, name="claude")
    assert result.tool is not None
    assert result.tool.name == "claude"


@pytest.mark.asyncio
async def test_show_tool_unknown(db_session):
    result = await show_tool(db_session, name="nope")
    assert result.tool is None


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_then_enable(db_session, guild_with_admin_role):
    d = await disable_tool(
        db_session,
        guild_id=guild_with_admin_role.guild_id,
        name="claude",
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    await db_session.commit()
    assert d.success and d.enabled is False

    e = await enable_tool(
        db_session,
        guild_id=guild_with_admin_role.guild_id,
        name="claude",
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    await db_session.commit()
    assert e.success and e.enabled is True


# ---------------------------------------------------------------------------
# update_tool_field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_field(db_session, guild_with_admin_role):
    r = await update_tool_field(
        db_session,
        guild_id=guild_with_admin_role.guild_id,
        name="claude",
        field="display_name",
        value="Claude (renamed)",
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    await db_session.commit()
    assert r.success


@pytest.mark.asyncio
async def test_update_field_rejects_immutable(db_session, guild_with_admin_role):
    r = await update_tool_field(
        db_session,
        guild_id=guild_with_admin_role.guild_id,
        name="claude",
        field="name",
        value="x",
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    assert r.success is False


# ---------------------------------------------------------------------------
# remove_tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_unknown_tool(db_session, guild_with_admin_role):
    r = await remove_tool(
        db_session,
        guild_id=guild_with_admin_role.guild_id,
        name="nope",
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    assert r.success is False


@pytest.mark.asyncio
async def test_update_tool_api_key_env(db_session, guild_with_admin_role):
    r = await update_tool_field(
        db_session,
        guild_id=guild_with_admin_role.guild_id,
        name="opencode",
        field="api_key_env",
        value="OPENCODE_API_KEY",
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    await db_session.commit()
    assert r.success is True
