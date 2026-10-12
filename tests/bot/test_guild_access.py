"""Guild allowlist and session-operator gates (audit 2026-10-12, finding #1)."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from discode.bot.policies import admin_auth


@pytest.fixture
def allow(monkeypatch):
    def _set(*, guilds: str = "", admins: str = "") -> None:
        settings = SimpleNamespace(
            allowed_guild_ids=frozenset(g for g in guilds.split(",") if g),
            platform_admin_user_ids=frozenset(a for a in admins.split(",") if a),
        )
        monkeypatch.setattr("discode.config.settings", settings)

    return _set


@pytest.mark.parametrize(
    ("guilds", "guild_id", "expected"),
    [
        ("", 1, False),  # empty allowlist fails closed
        ("1,2", 1, True),
        ("1,2", "2", True),
        ("1,2", 3, False),
        ("1", None, False),  # DMs are never allowed
    ],
)
def test_is_guild_allowed(allow, guilds, guild_id, expected):
    allow(guilds=guilds)
    assert admin_auth.is_guild_allowed(guild_id) is expected


@pytest.mark.asyncio
async def test_platform_admin_can_operate_without_guild_policy(allow, monkeypatch):
    allow(admins="42")
    guild_check = AsyncMock(return_value=False)
    monkeypatch.setattr(admin_auth, "is_guild_admin_authorized", guild_check)
    assert await admin_auth.can_operate_sessions(
        MagicMock(), guild_id="g", user_id="42", member_roles=[]
    )
    guild_check.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("is_admin", [True, False])
async def test_non_platform_user_defers_to_guild_admin_policy(allow, monkeypatch, is_admin):
    allow(admins="42")
    monkeypatch.setattr(admin_auth, "is_guild_admin_authorized", AsyncMock(return_value=is_admin))
    assert (
        await admin_auth.can_operate_sessions(
            MagicMock(), guild_id="g", user_id="7", member_roles=["r"]
        )
        is is_admin
    )


def _interaction(guild_id, kind=discord.InteractionType.application_command):
    return SimpleNamespace(
        guild_id=guild_id,
        user=SimpleNamespace(id=7),
        type=kind,
        response=SimpleNamespace(send_message=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_tree_refuses_unallowed_guild(allow):
    from discode.bot.main import DiscodeCommandTree

    allow(guilds="1")
    tree = DiscodeCommandTree.__new__(DiscodeCommandTree)
    interaction = _interaction(2)
    assert await tree.interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_tree_refuses_autocomplete_silently(allow):
    from discode.bot.main import DiscodeCommandTree

    allow(guilds="1")
    tree = DiscodeCommandTree.__new__(DiscodeCommandTree)
    interaction = _interaction(2, discord.InteractionType.autocomplete)
    assert await tree.interaction_check(interaction) is False
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_tree_allows_listed_guild(allow):
    from discode.bot.main import DiscodeCommandTree

    allow(guilds="1")
    tree = DiscodeCommandTree.__new__(DiscodeCommandTree)
    interaction = _interaction(1)
    assert await tree.interaction_check(interaction) is True
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("guilds", "left"), [("1", False), ("9", True)])
async def test_on_guild_join_leaves_unallowed(allow, guilds, left):
    from discode.bot.main import DiscodeBot

    allow(guilds=guilds)
    bot = DiscodeBot.__new__(DiscodeBot)
    guild = SimpleNamespace(id=1, leave=AsyncMock())
    await bot.on_guild_join(guild)
    assert guild.leave.await_count == (1 if left else 0)


@pytest.mark.asyncio
async def test_start_refused_for_non_operator(monkeypatch):
    import discode.bot.main as main
    import discode.db.engine as engine

    @contextlib.asynccontextmanager
    async def _db():
        yield SimpleNamespace()

    monkeypatch.setattr(engine, "get_shared_engine", lambda _: object())
    monkeypatch.setattr(engine, "make_session_factory", lambda _: _db)
    monkeypatch.setattr(admin_auth, "can_operate_sessions", AsyncMock(return_value=False))
    guild = SimpleNamespace(id=1, get_channel=MagicMock())
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=2, name="user", roles=[]),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await main._start_handler(interaction, "claude", "s", "/tmp", runner_host_id="h")
    guild.get_channel.assert_not_called()
    assert "admin" in interaction.followup.send.call_args.args[0].lower()


@pytest.mark.asyncio
async def test_start_at_quota_creates_no_thread(monkeypatch):
    """Audit 2026-10-12: a full session quota is reported before any thread is created."""
    import discode.bot.main as main
    import discode.bot.policies.tool_channel_map as mapping
    import discode.bot.sagas.create as create
    import discode.bot.setup_service as setup
    import discode.bot.start_validation as start_validation
    import discode.db.engine as engine

    definition = SimpleNamespace(enabled=True, resume_token_source="json_field")

    @contextlib.asynccontextmanager
    async def _db():
        yield SimpleNamespace(get=AsyncMock(return_value=definition))

    monkeypatch.setattr(engine, "get_shared_engine", lambda _: object())
    monkeypatch.setattr(engine, "make_session_factory", lambda _: _db)
    monkeypatch.setattr(admin_auth, "can_operate_sessions", AsyncMock(return_value=True))
    monkeypatch.setattr(mapping, "resolve_tool_channel", AsyncMock(return_value="123"))
    monkeypatch.setattr(setup, "resolve_alias_or_path", AsyncMock(return_value="/w"))
    monkeypatch.setattr(start_validation, "validate_workspace", AsyncMock(return_value="/w"))
    monkeypatch.setattr("discode.security.execution_policy.ensure_execution_allowed", lambda: None)
    monkeypatch.setattr(create, "active_session_count", AsyncMock(return_value=10**6))
    guild = SimpleNamespace(id=1, get_channel=MagicMock())
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=2, name="user", roles=[]),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await main._start_handler(interaction, "claude", "s", "/w", runner_host_id="h")
    guild.get_channel.assert_not_called()
    assert "limit" in interaction.followup.send.call_args.args[0].lower()
