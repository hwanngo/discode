"""Slash-command handler tests for /session config."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from discode.bot.main import _resolve_session_for_channel, _session_key_autocomplete

# ---------------------------------------------------------------------------
# _resolve_session_for_channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_thread_session_returns_none_for_unknown_channel():
    """A channel_id that has no matching session row should return None."""
    # Use a real-ish async session mock whose scalar() returns None
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    result = await _resolve_session_for_channel(db, "non-session-channel-999")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_thread_session_returns_session_row():
    """A channel_id that resolves should return the session row."""
    fake_session_row = MagicMock()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=fake_session_row)

    result = await _resolve_session_for_channel(db, "12345")
    assert result is fake_session_row


# ---------------------------------------------------------------------------
# _session_key_autocomplete
# ---------------------------------------------------------------------------


def test_session_key_autocomplete_returns_reserved_keys():
    """Empty current string should return all three reserved keys."""
    interaction = MagicMock()
    choices = _session_key_autocomplete(interaction, "")
    names = [c.name for c in choices]
    assert "model" in names
    assert "base_url" in names
    assert "api_key" in names


def test_session_key_autocomplete_filters_by_substring():
    """'url' should only match 'base_url'."""
    interaction = MagicMock()
    choices = _session_key_autocomplete(interaction, "url")
    names = [c.name for c in choices]
    assert names == ["base_url"]


def test_session_key_autocomplete_filters_by_prefix():
    """'mo' should only match 'model'."""
    interaction = MagicMock()
    choices = _session_key_autocomplete(interaction, "mo")
    names = [c.name for c in choices]
    assert names == ["model"]


def test_session_key_autocomplete_no_match_returns_empty():
    """A string matching nothing should return an empty list."""
    interaction = MagicMock()
    choices = _session_key_autocomplete(interaction, "zzz")
    assert choices == []


# ---------------------------------------------------------------------------
# /session config set — error wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_config_set_outside_thread_returns_ephemeral_error():
    """/session config set in a non-session channel should return ephemeral error."""
    from discode.bot.main import SessionConfigSubgroup

    group = SessionConfigSubgroup()
    set_cmd = group.get_command("set")

    interaction = MagicMock()
    interaction.channel_id = 999999
    interaction.user = MagicMock()
    interaction.user.roles = []
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    mock_engine = AsyncMock()
    with (
        patch(
            "discode.bot.main._resolve_session_for_channel",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("discode.db.engine.make_engine", return_value=mock_engine),
        patch("discode.db.engine.make_session_factory") as mock_sf,
    ):
        # Wire up the async context manager
        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_sf.return_value = MagicMock(return_value=session_ctx)

        await set_cmd.callback(group, interaction, key="model", value="gpt-4")

    interaction.response.send_message.assert_called_once()
    call_kwargs = interaction.response.send_message.call_args
    msg = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("content", "")
    assert "session thread" in msg.lower()
    assert call_kwargs.kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_session_config_set_service_failure_relays_message():
    """/session config set should relay the service failure message ephemerally."""
    from discode.bot.main import SessionConfigSubgroup

    group = SessionConfigSubgroup()
    set_cmd = group.get_command("set")

    interaction = MagicMock()
    interaction.channel_id = 12345
    interaction.user = MagicMock()
    interaction.user.roles = []
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    fake_sess_row = MagicMock()
    fake_sess_row.id = "sess-1"

    fake_result = MagicMock()
    fake_result.success = False
    fake_result.message = "You need the configured admin role."

    mock_engine = AsyncMock()
    with (
        patch(
            "discode.bot.main._resolve_session_for_channel",
            new_callable=AsyncMock,
            return_value=fake_sess_row,
        ),
        patch(
            "discode.bot.session_config_service.set_session_config",
            new_callable=AsyncMock,
            return_value=fake_result,
        ),
        patch("discode.db.engine.make_engine", return_value=mock_engine),
        patch("discode.db.engine.make_session_factory") as mock_sf,
    ):
        session_ctx = AsyncMock()
        db_mock = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=db_mock)
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_sf.return_value = MagicMock(return_value=session_ctx)

        await set_cmd.callback(group, interaction, key="model", value="gpt-4")

    interaction.response.send_message.assert_called_once()
    call_kwargs = interaction.response.send_message.call_args
    msg = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("content", "")
    assert "admin role" in msg
    assert call_kwargs.kwargs.get("ephemeral") is True
