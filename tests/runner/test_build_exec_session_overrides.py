"""Tests for the per-session override layer in build_exec_session.

build_exec_session injects:
- argv: [tool_def.model_flag, model_value] right after argv_prefix when both set
- env: {tool_def.api_key_env: api_key} and {tool_def.base_url_env: base_url} when set
"""

from __future__ import annotations

import pytest

from discode.bot.session_config_service import set_session_config
from discode.db.models import SessionConfig
from discode.runner.saga_input import InputJobPayload, build_exec_session
from discode.tools.errors import BadSessionConfig

# guild_with_admin_role and seed_session_in_guild come from tests/conftest.py


def _store_entry(session) -> dict:
    return {
        "sid": session.id,
        "tool": session.tool,
        "cwd": session.cwd,
        "tool_resume_token": None,
        "env": {},
        "guild_id": session.guild_id,
        "thread_id": str(session.id),
    }


def _payload(session) -> InputJobPayload:
    return InputJobPayload(
        session_id=str(session.id),
        guild_id=str(session.guild_id),
        thread_id=str(session.id),
        host_id="h",
        text="hi",
        idempotency_key="k1",
    )


@pytest.mark.asyncio
async def test_no_overrides_argv_unchanged(db_session, seed_session_in_guild):
    sess = await build_exec_session(
        _store_entry(seed_session_in_guild), _payload(seed_session_in_guild), db_session
    )
    assert sess._argv[0].endswith("claude") or "claude" in sess._argv[0]
    assert "--model" not in sess._argv


@pytest.mark.asyncio
async def test_model_override_splices_into_argv(
    db_session, guild_with_admin_role, seed_session_in_guild
):
    auth = dict(
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    await set_session_config(
        db_session,
        session_id=seed_session_in_guild.id,
        key="model",
        value="claude-3-5-haiku",
        **auth,
    )
    await db_session.commit()

    sess = await build_exec_session(
        _store_entry(seed_session_in_guild), _payload(seed_session_in_guild), db_session
    )
    assert "--model" in sess._argv
    idx = sess._argv.index("--model")
    assert sess._argv[idx + 1] == "claude-3-5-haiku"
    # Claude argv_prefix is 6 tokens; model flag must be inserted right after them.
    assert idx == 6


@pytest.mark.asyncio
async def test_base_url_override_injected_into_env(
    db_session, guild_with_admin_role, seed_session_in_guild
):
    auth = dict(
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    await set_session_config(
        db_session,
        session_id=seed_session_in_guild.id,
        key="base_url",
        value="http://localhost:11434",
        **auth,
    )
    await db_session.commit()

    sess = await build_exec_session(
        _store_entry(seed_session_in_guild), _payload(seed_session_in_guild), db_session
    )
    assert sess._env.get("ANTHROPIC_BASE_URL") == "http://localhost:11434"


@pytest.mark.asyncio
async def test_api_key_override_decrypted_into_env(
    db_session, guild_with_admin_role, seed_session_in_guild
):
    auth = dict(
        member_roles=guild_with_admin_role.admin_role_ids,
        member_role_names=guild_with_admin_role.admin_role_names,
    )
    await set_session_config(
        db_session,
        session_id=seed_session_in_guild.id,
        key="api_key",
        value="sk-test-secret",
        **auth,
    )
    await db_session.commit()

    sess = await build_exec_session(
        _store_entry(seed_session_in_guild), _payload(seed_session_in_guild), db_session
    )
    assert sess._env.get("ANTHROPIC_API_KEY") == "sk-test-secret"


@pytest.mark.asyncio
async def test_corrupt_ciphertext_raises_bad_session_config(db_session, seed_session_in_guild):
    db_session.add(
        SessionConfig(
            session_id=seed_session_in_guild.id,
            key="api_key",
            value=None,
            value_ciphertext="garbage-not-fernet",
        )
    )
    await db_session.commit()

    with pytest.raises(BadSessionConfig) as exc:
        await build_exec_session(
            _store_entry(seed_session_in_guild), _payload(seed_session_in_guild), db_session
        )
    assert "api_key" in exc.value.reason
