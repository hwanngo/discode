"""Discord UX regressions (audit 2026-10-12, finding #10)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from discode.dispatcher.main import _render_notice


@pytest.mark.parametrize(
    ("envelope", "expected"),
    [
        ({"notice_type": "session_running", "message": ""}, "Session ready"),
        ({"notice_type": "session_create_failed", "message": "creation_timeout"}, "in time"),
        ({"notice_type": "session_create_failed", "message": "cwd_invalid"}, "allowed root"),
        ({"notice_type": "session_create_failed", "message": "weird"}, "weird"),
        ({"message": "plain text"}, "plain text"),
    ],
)
def test_render_notice(envelope, expected):
    assert expected in _render_notice(envelope)


def test_render_notice_keeps_empty_for_unknown_types():
    assert _render_notice({"notice_type": "other", "message": ""}) == ""


@pytest.mark.asyncio
async def test_app_command_error_handler_survives_expired_interaction():
    from discode.bot.main import DiscodeBot

    bot = DiscodeBot.__new__(DiscodeBot)
    response = MagicMock()
    response.is_done.return_value = False
    response.send_message = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404, reason="x"), "Unknown interaction")
    )
    interaction = SimpleNamespace(response=response, followup=MagicMock())
    await bot.on_app_command_error(interaction, discord.app_commands.AppCommandError("boom"))


@pytest.mark.asyncio
async def test_joined_allowed_guild_gets_commands(monkeypatch):
    from discode.bot.main import DiscodeBot

    monkeypatch.setattr("discode.bot.policies.admin_auth.is_guild_allowed", lambda guild_id: True)
    bot = DiscodeBot.__new__(DiscodeBot)
    bot._guild_admin_commands = [SimpleNamespace(name="setup")]
    tree = MagicMock()
    tree.sync = AsyncMock()
    monkeypatch.setattr(DiscodeBot, "tree", property(lambda self: tree))
    await bot.on_guild_join(SimpleNamespace(id=5, leave=AsyncMock()))
    tree.add_command.assert_called_once()
    assert tree.sync.await_args.kwargs["guild"].id == 5
