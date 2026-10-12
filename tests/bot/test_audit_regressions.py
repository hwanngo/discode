from __future__ import annotations

import contextlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from discode.bot import tool_service
from discode.bot.sagas.input import run_input_saga
from discode.bot.session_service import resolve_session_target
from discode.db.models import InputTurn, ToolDefinition


def factory_for(db):
    @contextlib.asynccontextmanager
    async def context():
        yield db

    return context


def tool_row():
    return ToolDefinition(
        name="safe",
        display_name="Safe",
        argv_prefix=["agent"],
        argv_resume_tokens=[],
        argv_suffix=["{prompt}"],
        uses_pty=False,
        resume_token_source="none",
        resume_token_pattern=None,
        reply_extractor="stdout_raw",
        reply_json_field=None,
        extra_paths=[],
        env_passthrough=[],
        model_flag=None,
        api_key_env=None,
        base_url_env=None,
    )


@pytest.mark.asyncio
async def test_rejected_edit_does_not_mutate_shared_tool(monkeypatch):
    monkeypatch.setattr(tool_service, "_platform_authorized", lambda actor: actor == "platform")
    row = tool_row()
    db = SimpleNamespace(get=AsyncMock(return_value=row))
    result = await tool_service.update_tool_field(
        db,
        guild_id="guild",
        name="safe",
        field="argv_suffix",
        value="[5]",
        member_roles=["guild-admin"],
        user_id="platform",
    )
    assert not result.success
    assert row.argv_suffix == ["{prompt}"]


@pytest.mark.asyncio
async def test_guild_admin_cannot_mutate_global_tools(monkeypatch):
    import discode.config

    monkeypatch.setattr(
        discode.config, "settings", SimpleNamespace(platform_admin_user_ids={"platform"})
    )
    db = SimpleNamespace(get=AsyncMock())
    result = await tool_service.update_tool_field(
        db,
        guild_id="guild",
        name="safe",
        field="argv_prefix",
        value='["evil"]',
        member_roles=["guild-admin"],
        user_id="guild-admin",
    )
    assert not result.authorized
    db.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_boolean_is_not_silently_false(monkeypatch):
    monkeypatch.setattr(tool_service, "_platform_authorized", lambda actor: True)
    row = tool_row()
    row.uses_pty = True
    result = await tool_service.update_tool_field(
        SimpleNamespace(get=AsyncMock(return_value=row)),
        guild_id="g",
        name="safe",
        field="uses_pty",
        value="perhaps",
        member_roles=[],
        user_id="platform",
    )
    assert not result.success
    assert row.uses_pty


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["creating", "resuming", "stopping", "stopped", "archived"])
async def test_input_rejects_inactive_state_before_outbox(monkeypatch, status):
    import discode.bot.sagas.input as saga
    import discode.config

    monkeypatch.setattr(discode.config, "settings", SimpleNamespace(EMERGENCY_STOP=0))
    row = SimpleNamespace(guild_id="g", status=status)
    db = SimpleNamespace(get=AsyncMock(return_value=row), add=MagicMock())
    db.begin = factory_for(db)
    write = AsyncMock()
    monkeypatch.setattr(saga, "write_outbox", write)
    with pytest.raises(ValueError, match="session_not_ready"):
        await run_input_saga(
            factory_for(db),
            session_id=str(uuid.uuid4()),
            guild_id="g",
            thread_id="t",
            host_id="h",
            owner_id="u",
            text_content="hi",
        )
    write.assert_not_awaited()


@pytest.mark.asyncio
async def test_input_enqueues_durable_turn_and_refreshes_activity(monkeypatch):
    import discode.bot.sagas.input as saga
    import discode.config

    monkeypatch.setattr(discode.config, "settings", SimpleNamespace(EMERGENCY_STOP=0))
    row = SimpleNamespace(
        id=uuid.uuid4(),
        guild_id="g",
        status="idle",
        thread_id="t",
        host_id="h",
        lifecycle_version=4,
        last_activity_at=None,
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=row),
        add=MagicMock(),
        execute=AsyncMock(
            return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
        ),
    )
    db.begin = factory_for(db)
    write = AsyncMock()
    monkeypatch.setattr(saga, "write_outbox", write)
    key = await run_input_saga(
        factory_for(db),
        session_id=str(row.id),
        guild_id="g",
        thread_id="wrong",
        host_id="wrong",
        owner_id="u",
        text_content="hi",
        actor_id="invitee",
    )
    events = [call.args[0] for call in db.add.call_args_list if hasattr(call.args[0], "actor_id")]
    assert events and all(event.actor_id == "invitee" for event in events)
    turns = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], InputTurn)]
    assert len(turns) == 1 and turns[0].idempotency_key == key
    assert turns[0].lifecycle_version == 4
    assert row.last_activity_at is not None and row.status == "running"
    assert write.call_args.kwargs["payload"]["thread_id"] == "t"
    assert write.call_args.kwargs["payload"]["lifecycle_version"] == 4


@pytest.mark.asyncio
async def test_ambiguous_session_prefix_is_rejected(monkeypatch):
    import discode.bot.policies.admin_auth as auth

    monkeypatch.setattr(auth, "is_guild_admin_authorized", AsyncMock(return_value=True))
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [object(), object()])
            )
        )
    )
    assert (
        await resolve_session_target(
            factory_for(db), guild_id="g", user_id="u", session_ref="deadbeef"
        )
        is None
    )


@pytest.mark.asyncio
async def test_current_thread_wins_over_latest_session(monkeypatch):
    import discode.bot.policies.admin_auth as auth

    monkeypatch.setattr(auth, "is_guild_admin_authorized", AsyncMock(return_value=True))
    row = SimpleNamespace(
        id=uuid.uuid4(), name="current", cwd="/tmp", thread_id="thread", host_id="h", owner_id="u"
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: row))
    )
    result = await resolve_session_target(
        factory_for(db), guild_id="g", user_id="u", session_ref=None, thread_id="thread"
    )
    assert result["session_id"] == str(row.id)
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_invalid_alias_does_not_create_discord_thread(monkeypatch):
    import discode.bot.main as main
    import discode.bot.policies.admin_auth as admin_auth
    import discode.bot.policies.tool_channel_map as mapping
    import discode.bot.setup_service as setup
    import discode.db.engine as engine

    db = SimpleNamespace()
    monkeypatch.setattr(engine, "get_shared_engine", lambda _: object())
    monkeypatch.setattr(engine, "make_session_factory", lambda _: factory_for(db))
    monkeypatch.setattr(admin_auth, "can_operate_sessions", AsyncMock(return_value=True))
    monkeypatch.setattr(mapping, "resolve_tool_channel", AsyncMock(return_value="123"))
    monkeypatch.setattr(
        setup, "resolve_alias_or_path", AsyncMock(side_effect=ValueError("unknown_alias"))
    )
    guild = SimpleNamespace(id=1, get_channel=MagicMock())
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=2, name="user"),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await main._start_handler(interaction, "safe", "session", "missing", runner_host_id="h")
    guild.get_channel.assert_not_called()
    assert "unknown_alias" in interaction.followup.send.call_args.args[0]


@pytest.mark.asyncio
async def test_invalid_tool_edit_survives_commit_without_changing_row(db_session, monkeypatch):
    monkeypatch.setattr(tool_service, "_platform_authorized", lambda actor: True)
    row = await db_session.get(ToolDefinition, "claude")
    original = list(row.argv_suffix)
    result = await tool_service.update_tool_field(
        db_session,
        guild_id="g",
        name="claude",
        field="argv_suffix",
        value='["bad"]',
        member_roles=[],
        user_id="platform",
    )
    assert not result.success
    await db_session.commit()
    await db_session.refresh(row)
    assert row.argv_suffix == original
