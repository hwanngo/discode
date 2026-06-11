"""Tests for /session invite slash command (Phase 2a Task 3)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _build_interaction(user_id: str = "5678"):
    interaction = MagicMock()
    interaction.guild_id = 1234
    interaction.user.id = int(user_id)
    interaction.user.roles = []
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _make_session_ctx(db_mock):
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=db_mock)
    session_ctx.__aexit__ = AsyncMock(return_value=False)
    return session_ctx


@pytest.mark.asyncio
async def test_invite_no_session_resolved_returns_friendly_error():
    from discode.bot.main import SessionCommands

    group = SessionCommands()
    invite_cmd = group.get_command("invite")
    interaction = _build_interaction()
    user = MagicMock(id=9999, mention="<@9999>", __str__=lambda self: "invitee#0001")

    with (
        patch("discode.config.settings") as mock_settings,
        patch("discode.db.engine.make_engine") as mock_engine,
        patch("discode.db.engine.make_session_factory") as mock_factory,
        patch(
            "discode.bot.session_service.resolve_session_target",
            new=AsyncMock(return_value=None),
        ),
    ):
        mock_settings.database_url = "postgresql+asyncpg://x"
        mock_engine.return_value = MagicMock()
        mock_engine.return_value.dispose = AsyncMock()
        mock_factory.return_value = MagicMock()
        await invite_cmd.callback(group, interaction, user=user, session=None)

    msg = interaction.followup.send.call_args.args[0]
    assert "No matching session" in msg


@pytest.mark.asyncio
async def test_invite_non_owner_non_admin_rejected():
    from discode.bot.main import SessionCommands

    group = SessionCommands()
    invite_cmd = group.get_command("invite")
    interaction = _build_interaction(user_id="5678")
    user = MagicMock(id=9999, mention="<@9999>", __str__=lambda self: "invitee#0001")

    fake_target = {
        "session_id": "abc",
        "host_id": "h",
        "name": "n",
        "tool": "claude",
        "thread_id": "t",
        "owner_id": "different-owner",
    }
    fake_sess_row = MagicMock(owner_id="different-owner")
    db_mock = AsyncMock()
    db_mock.get = AsyncMock(return_value=fake_sess_row)

    with (
        patch("discode.config.settings") as mock_settings,
        patch("discode.db.engine.make_engine") as mock_engine,
        patch("discode.db.engine.make_session_factory") as mock_factory,
        patch(
            "discode.bot.session_service.resolve_session_target",
            new=AsyncMock(return_value=fake_target),
        ),
        patch(
            "discode.bot.policies.admin_auth.is_guild_admin_authorized",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "discode.bot.session_service.invite_member",
            new=AsyncMock(return_value=True),
        ) as mock_invite,
    ):
        mock_settings.database_url = "postgresql+asyncpg://x"
        mock_engine.return_value = MagicMock()
        mock_engine.return_value.dispose = AsyncMock()
        factory = MagicMock(return_value=_make_session_ctx(db_mock))
        mock_factory.return_value = factory

        await invite_cmd.callback(group, interaction, user=user, session=None)

    mock_invite.assert_not_awaited()
    msg = interaction.followup.send.call_args.args[0]
    assert "owner" in msg.lower() or "admin" in msg.lower()


@pytest.mark.asyncio
async def test_invite_owner_succeeds():
    from discode.bot.main import SessionCommands

    group = SessionCommands()
    invite_cmd = group.get_command("invite")
    interaction = _build_interaction(user_id="5678")
    user = MagicMock(id=9999, mention="<@9999>", __str__=lambda self: "invitee#0001")

    fake_target = {
        "session_id": "abc",
        "host_id": "h",
        "name": "n",
        "tool": "claude",
        "thread_id": "t",
        "owner_id": "5678",
    }
    fake_sess_row = MagicMock(owner_id="5678")
    db_mock = AsyncMock()
    db_mock.get = AsyncMock(return_value=fake_sess_row)

    with (
        patch("discode.config.settings") as mock_settings,
        patch("discode.db.engine.make_engine") as mock_engine,
        patch("discode.db.engine.make_session_factory") as mock_factory,
        patch(
            "discode.bot.session_service.resolve_session_target",
            new=AsyncMock(return_value=fake_target),
        ),
        patch(
            "discode.bot.session_service.invite_member",
            new=AsyncMock(return_value=True),
        ) as mock_invite,
    ):
        mock_settings.database_url = "postgresql+asyncpg://x"
        mock_engine.return_value = MagicMock()
        mock_engine.return_value.dispose = AsyncMock()
        factory = MagicMock(return_value=_make_session_ctx(db_mock))
        mock_factory.return_value = factory

        await invite_cmd.callback(group, interaction, user=user, session=None)

    mock_invite.assert_awaited_once()
    msg = interaction.followup.send.call_args.args[0]
    assert "invited" in msg.lower()
