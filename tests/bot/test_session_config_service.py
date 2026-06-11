"""Tests for session_config service helpers (Task E5)."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from discode.bot.session_config_service import (
    clear_all_session_config,
    clear_session_config_key,
    get_session_config,
    set_session_config,
)
from discode.db.models import Session, SessionConfig

# guild_with_admin_role and seed_session_in_guild come from tests/conftest.py


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_session_in_guild_for_opencode(db_session, guild_with_admin_role):
    """Insert a Session row with tool='opencode' (api_key_env = NULL)."""
    sid = uuid.uuid4()
    sess = Session(
        id=sid,
        guild_id=guild_with_admin_role.guild_id,
        tool="opencode",
        name=f"test-session-{sid.hex[:6]}",
        cwd="/tmp",
        cwd_realpath="/tmp",
        hook_secret_hash="fakehash",
        status="idle",
    )
    db_session.add(sess)
    await db_session.flush()
    return sess


# ---------------------------------------------------------------------------
# set_session_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_model_happy(db_session, guild_with_admin_role, seed_session_in_guild):
    r = await set_session_config(
        db_session,
        session_id=seed_session_in_guild.id,
        key="model",
        value="claude-3-5-haiku",
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    await db_session.commit()
    assert r.authorized is True and r.success is True
    row = await db_session.scalar(
        select(SessionConfig).where(
            SessionConfig.session_id == seed_session_in_guild.id,
            SessionConfig.key == "model",
        )
    )
    assert row.value == "claude-3-5-haiku"
    assert row.value_ciphertext is None


@pytest.mark.asyncio
async def test_set_api_key_encrypted(db_session, guild_with_admin_role, seed_session_in_guild):
    r = await set_session_config(
        db_session,
        session_id=seed_session_in_guild.id,
        key="api_key",
        value="sk-secret-abc",
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    await db_session.commit()
    assert r.success is True
    row = await db_session.scalar(
        select(SessionConfig).where(
            SessionConfig.session_id == seed_session_in_guild.id,
            SessionConfig.key == "api_key",
        )
    )
    assert row.value is None
    assert row.value_ciphertext is not None
    assert "sk-secret-abc" not in row.value_ciphertext


@pytest.mark.asyncio
async def test_set_unauthorized(db_session, guild_with_admin_role, seed_session_in_guild):
    r = await set_session_config(
        db_session,
        session_id=seed_session_in_guild.id,
        key="model",
        value="X",
        member_roles=[],
        member_role_names=["member"],
    )
    assert r.authorized is False


@pytest.mark.asyncio
async def test_set_unknown_key(db_session, guild_with_admin_role, seed_session_in_guild):
    r = await set_session_config(
        db_session,
        session_id=seed_session_in_guild.id,
        key="bogus",
        value="X",
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    assert r.success is False


@pytest.mark.asyncio
async def test_set_invalid_value(db_session, guild_with_admin_role, seed_session_in_guild):
    r = await set_session_config(
        db_session,
        session_id=seed_session_in_guild.id,
        key="base_url",
        value="not a url",
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    assert r.success is False


@pytest.mark.asyncio
async def test_set_rejects_when_tool_mapping_null(
    db_session, guild_with_admin_role, seed_session_in_guild_for_opencode
):
    # opencode has api_key_env = NULL in the seed
    r = await set_session_config(
        db_session,
        session_id=seed_session_in_guild_for_opencode.id,
        key="api_key",
        value="sk-x",
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    assert r.success is False
    assert "support" in r.message.lower()


@pytest.mark.asyncio
async def test_set_upsert(db_session, guild_with_admin_role, seed_session_in_guild):
    sid = seed_session_in_guild.id
    auth = dict(
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    await set_session_config(db_session, session_id=sid, key="model", value="alpha", **auth)
    await set_session_config(db_session, session_id=sid, key="model", value="beta", **auth)
    await db_session.commit()
    row = await db_session.scalar(
        select(SessionConfig).where(
            SessionConfig.session_id == sid,
            SessionConfig.key == "model",
        )
    )
    assert row.value == "beta"


# ---------------------------------------------------------------------------
# get_session_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_set_keys_with_secret_masked(
    db_session, guild_with_admin_role, seed_session_in_guild
):
    sid = seed_session_in_guild.id
    auth = dict(
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    await set_session_config(db_session, session_id=sid, key="model", value="claude-3", **auth)
    await set_session_config(db_session, session_id=sid, key="api_key", value="sk-secret", **auth)
    await db_session.commit()
    r = await get_session_config(db_session, session_id=sid)
    rendered = {row.key: row.display_value for row in r.rows}
    assert rendered["model"] == "claude-3"
    assert rendered["api_key"] == "<set>"


@pytest.mark.asyncio
async def test_get_empty(db_session, seed_session_in_guild):
    r = await get_session_config(db_session, session_id=seed_session_in_guild.id)
    assert r.rows == []


# ---------------------------------------------------------------------------
# clear_session_config_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_one(db_session, guild_with_admin_role, seed_session_in_guild):
    sid = seed_session_in_guild.id
    auth = dict(
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    await set_session_config(db_session, session_id=sid, key="model", value="v", **auth)
    await db_session.commit()
    r = await clear_session_config_key(db_session, session_id=sid, key="model", **auth)
    await db_session.commit()
    assert r.success is True and r.deleted is True


@pytest.mark.asyncio
async def test_clear_unset_is_idempotent(db_session, guild_with_admin_role, seed_session_in_guild):
    r = await clear_session_config_key(
        db_session,
        session_id=seed_session_in_guild.id,
        key="model",
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    assert r.success is True and r.deleted is False


# ---------------------------------------------------------------------------
# clear_all_session_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_all(db_session, guild_with_admin_role, seed_session_in_guild):
    sid = seed_session_in_guild.id
    auth = dict(
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    await set_session_config(db_session, session_id=sid, key="model", value="v", **auth)
    await set_session_config(db_session, session_id=sid, key="base_url", value="https://x", **auth)
    await db_session.commit()
    r = await clear_all_session_config(db_session, session_id=sid, **auth)
    await db_session.commit()
    assert r.success is True and r.count == 2
