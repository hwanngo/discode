"""Tests for /send slash command (Phase 2b)."""

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
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_send_empty_text_rejected():
    from discode.bot.main import build_send_command

    send_cmd = build_send_command()
    interaction = _build_interaction()
    await send_cmd.callback(interaction, text="   ", session=None)
    msg = interaction.response.send_message.call_args.args[0]
    assert "Text is required" in msg


@pytest.mark.asyncio
async def test_send_no_session_resolved():
    from discode.bot.main import build_send_command

    send_cmd = build_send_command()
    interaction = _build_interaction()

    with (
        patch("discode.db.engine.make_engine") as mock_engine,
        patch("discode.db.engine.make_session_factory") as mock_factory,
        patch(
            "discode.bot.session_service.resolve_session_target",
            new=AsyncMock(return_value=None),
        ),
    ):
        mock_engine.return_value = MagicMock()
        mock_engine.return_value.dispose = AsyncMock()
        mock_factory.return_value = MagicMock()
        await send_cmd.callback(interaction, text="hi", session=None)

    msg = interaction.followup.send.call_args.args[0]
    assert "No matching session" in msg


@pytest.mark.asyncio
async def test_send_non_member_non_admin_rejected():
    from discode.bot.main import build_send_command

    send_cmd = build_send_command()
    interaction = _build_interaction()

    fake_target = {
        "session_id": "abc",
        "host_id": "h",
        "name": "n",
        "tool": "claude",
        "thread_id": "t",
        "owner_id": "different-owner",
    }

    with (
        patch("discode.db.engine.make_engine") as mock_engine,
        patch("discode.db.engine.make_session_factory") as mock_factory,
        patch(
            "discode.bot.session_service.resolve_session_target",
            new=AsyncMock(return_value=fake_target),
        ),
        patch(
            "discode.bot.session_service.is_session_member",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "discode.bot.policies.admin_auth.is_guild_admin_authorized",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "discode.bot.session_service.send_input_for_session",
            new=AsyncMock(return_value="key-x"),
        ) as mock_send,
    ):
        mock_engine.return_value = MagicMock()
        mock_engine.return_value.dispose = AsyncMock()
        db_mock = AsyncMock()
        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=db_mock)
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = MagicMock(return_value=session_ctx)

        await send_cmd.callback(interaction, text="hello", session=None)

    mock_send.assert_not_awaited()
    msg = interaction.followup.send.call_args.args[0]
    assert "members" in msg.lower() or "admin" in msg.lower()


@pytest.mark.asyncio
async def test_send_member_succeeds():
    from discode.bot.main import build_send_command

    send_cmd = build_send_command()
    interaction = _build_interaction()

    fake_target = {
        "session_id": "abc",
        "host_id": "h",
        "name": "n",
        "tool": "claude",
        "thread_id": "t",
        "owner_id": "5678",
    }

    with (
        patch("discode.db.engine.make_engine") as mock_engine,
        patch("discode.db.engine.make_session_factory") as mock_factory,
        patch(
            "discode.bot.session_service.resolve_session_target",
            new=AsyncMock(return_value=fake_target),
        ),
        patch(
            "discode.bot.session_service.is_session_member",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "discode.bot.session_service.send_input_for_session",
            new=AsyncMock(return_value="key-abc12345"),
        ) as mock_send,
    ):
        mock_engine.return_value = MagicMock()
        mock_engine.return_value.dispose = AsyncMock()
        db_mock = AsyncMock()
        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=db_mock)
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = MagicMock(return_value=session_ctx)

        await send_cmd.callback(interaction, text="hello", session=None)

    mock_send.assert_awaited_once()
    msg = interaction.followup.send.call_args.args[0]
    assert "queued" in msg.lower()
