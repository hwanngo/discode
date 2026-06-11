"""Tests for /root add with optional alias param (Phase 1)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_root_add_no_alias_admin_check_skipped():
    """When no alias is provided, the admin gate is NOT consulted."""
    from discode.bot.main import RootCommands

    cmds = RootCommands()
    add_cmd = cmds.get_command("add")

    interaction = MagicMock()
    interaction.guild_id = 1234
    interaction.user.id = 5678
    interaction.user.roles = []
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    fake_root_result = MagicMock(realpath="/tmp/x")

    with (
        patch("discode.db.engine.make_engine") as mock_engine,
        patch("discode.db.engine.make_session_factory") as mock_factory,
        patch(
            "discode.bot.setup_service.add_allowed_root_for_user",
            new=AsyncMock(return_value=fake_root_result),
        ),
        patch(
            "discode.bot.policies.admin_auth.is_guild_admin_authorized",
            new=AsyncMock(return_value=False),  # gate would reject if called
        ) as mock_admin_check,
    ):
        mock_engine.return_value = MagicMock()
        mock_engine.return_value.dispose = AsyncMock()
        mock_factory.return_value = MagicMock()
        await add_cmd.callback(cmds, interaction, path="/tmp/x")

    mock_admin_check.assert_not_awaited()
    interaction.followup.send.assert_called_once()
    msg = interaction.followup.send.call_args.args[0]
    assert "/tmp/x" in msg


@pytest.mark.asyncio
async def test_root_add_with_alias_admin_required():
    """When alias is provided and caller is non-admin, NO writes happen."""
    from discode.bot.main import RootCommands

    cmds = RootCommands()
    add_cmd = cmds.get_command("add")

    interaction = MagicMock()
    interaction.guild_id = 1234
    interaction.user.id = 5678
    interaction.user.roles = []
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    add_root_mock = AsyncMock()
    set_alias_mock = AsyncMock()

    with (
        patch("discode.db.engine.make_engine") as mock_engine,
        patch("discode.db.engine.make_session_factory") as mock_factory,
        patch("discode.bot.setup_service.add_allowed_root_for_user", new=add_root_mock),
        patch("discode.bot.setup_service.set_path_alias", new=set_alias_mock),
        patch(
            "discode.bot.policies.admin_auth.is_guild_admin_authorized",
            new=AsyncMock(return_value=False),
        ),
    ):
        mock_engine.return_value = MagicMock()
        mock_engine.return_value.dispose = AsyncMock()
        mock_factory.return_value = MagicMock()
        await add_cmd.callback(cmds, interaction, path="/tmp/x", alias="myalias")

    add_root_mock.assert_not_awaited()
    set_alias_mock.assert_not_awaited()
    msg = interaction.followup.send.call_args.args[0]
    assert "admin" in msg.lower()


@pytest.mark.asyncio
async def test_root_add_with_alias_admin_succeeds():
    """When alias is provided and caller IS admin, both root and alias are written."""
    from discode.bot.main import RootCommands

    cmds = RootCommands()
    add_cmd = cmds.get_command("add")

    interaction = MagicMock()
    interaction.guild_id = 1234
    interaction.user.id = 5678
    interaction.user.roles = []
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    fake_root_result = MagicMock(realpath="/tmp/x")
    fake_alias_result = MagicMock(alias="myalias", realpath="/tmp/x")

    with (
        patch("discode.db.engine.make_engine") as mock_engine,
        patch("discode.db.engine.make_session_factory") as mock_factory,
        patch(
            "discode.bot.setup_service.add_allowed_root_for_user",
            new=AsyncMock(return_value=fake_root_result),
        ),
        patch(
            "discode.bot.setup_service.set_path_alias",
            new=AsyncMock(return_value=fake_alias_result),
        ),
        patch(
            "discode.bot.policies.admin_auth.is_guild_admin_authorized",
            new=AsyncMock(return_value=True),
        ),
    ):
        mock_engine.return_value = MagicMock()
        mock_engine.return_value.dispose = AsyncMock()
        mock_factory.return_value = MagicMock()
        await add_cmd.callback(cmds, interaction, path="/tmp/x", alias="myalias")

    msg = interaction.followup.send.call_args.args[0]
    assert "myalias" in msg
    assert "/tmp/x" in msg
