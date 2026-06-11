"""Tests for the /tool slash command subgroup and /start autocomplete."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_interaction(guild_id="1234", user_id="42", role_ids=None, role_names=None):
    """Build a minimal mock discord.Interaction."""
    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.guild.id = int(guild_id)
    interaction.guild_id = int(guild_id)

    role_ids = role_ids or []
    role_names = role_names or []
    roles = []
    for rid, rname in zip(role_ids, role_names):
        r = MagicMock()
        r.id = int(rid)
        r.name = rname
        roles.append(r)

    member = MagicMock()
    member.id = int(user_id)
    member.roles = roles
    interaction.user = member

    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    return interaction


@dataclass
class _FakeTool:
    name: str
    display_name: str
    enabled: bool


def _make_session_factory_mock():
    """Return a (mock_make_session_factory, async context manager mock) pair."""
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    session_ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=session_ctx)
    mock_make_sf = MagicMock(return_value=factory)
    return mock_make_sf


# ---------------------------------------------------------------------------
# /tool register — JSON parse error path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_register_bad_json_returns_error():
    """Invalid JSON arg should produce an ephemeral error without calling the service."""
    from discode.bot.main import ToolCommands

    group = ToolCommands()
    interaction = _make_interaction()
    # Discord wraps the method as a Command; access the underlying coroutine via .callback
    register_cmd = group.get_command("register")

    with patch("discode.bot.tool_service.register_tool") as mock_svc:
        await register_cmd.callback(group, interaction, name="mytool", config="not json {{{")

    mock_svc.assert_not_called()
    interaction.response.send_message.assert_called_once()
    call_kwargs = interaction.response.send_message.call_args
    msg = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("content", "")
    assert "Invalid JSON" in msg
    assert call_kwargs.kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# /tool list — formatting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_list_formats_output():
    """list_tools result should be formatted as name • display_name • enabled/disabled."""
    from discode.bot.main import ToolCommands

    group = ToolCommands()
    interaction = _make_interaction()
    list_cmd = group.get_command("list")

    fake_result = MagicMock()
    fake_result.tools = [
        _FakeTool("claude", "Claude", True),
        _FakeTool("codex", "Codex", False),
    ]

    with patch(
        "discode.bot.tool_service.list_tools", new_callable=AsyncMock, return_value=fake_result
    ):
        await list_cmd.callback(group, interaction)

    interaction.response.send_message.assert_called_once()
    call_kwargs = interaction.response.send_message.call_args
    msg = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("content", "")
    assert "claude" in msg
    assert "codex" in msg
    assert "enabled" in msg
    assert "disabled" in msg
    assert call_kwargs.kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_tool_list_empty():
    """Empty tool list should return a friendly message."""
    from discode.bot.main import ToolCommands

    group = ToolCommands()
    interaction = _make_interaction()
    list_cmd = group.get_command("list")

    fake_result = MagicMock()
    fake_result.tools = []

    with patch(
        "discode.bot.tool_service.list_tools", new_callable=AsyncMock, return_value=fake_result
    ):
        await list_cmd.callback(group, interaction)

    call_kwargs = interaction.response.send_message.call_args
    msg = (
        call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("content", "")
    ).lower()
    assert "no tools" in msg or "0" in msg or "empty" in msg


# ---------------------------------------------------------------------------
# /start autocomplete — unit test the standalone helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_autocomplete_filters_by_current():
    """Autocomplete should return only enabled tools matching `current`."""
    from discode.bot.main import _tool_autocomplete

    fake_result = MagicMock()
    fake_result.tools = [
        _FakeTool("claude", "Claude", True),
        _FakeTool("codex", "Codex", True),
        _FakeTool("opencode", "OpenCode", False),  # disabled → excluded
    ]

    fake_interaction = MagicMock()

    with patch(
        "discode.bot.tool_service.list_tools", new_callable=AsyncMock, return_value=fake_result
    ):
        choices = await _tool_autocomplete(fake_interaction, "cl")

    names = [c.value for c in choices]
    assert "claude" in names
    assert "codex" not in names  # doesn't match "cl"
    assert "opencode" not in names  # disabled


@pytest.mark.asyncio
async def test_tool_autocomplete_empty_current_returns_all_enabled():
    """Empty `current` string should return all enabled tools."""
    from discode.bot.main import _tool_autocomplete

    fake_result = MagicMock()
    fake_result.tools = [
        _FakeTool("claude", "Claude", True),
        _FakeTool("codex", "Codex", True),
        _FakeTool("pi", "Pi (disabled)", False),
    ]

    fake_interaction = MagicMock()

    with patch(
        "discode.bot.tool_service.list_tools", new_callable=AsyncMock, return_value=fake_result
    ):
        choices = await _tool_autocomplete(fake_interaction, "")

    values = [c.value for c in choices]
    assert "claude" in values
    assert "codex" in values
    assert "pi" not in values  # disabled
