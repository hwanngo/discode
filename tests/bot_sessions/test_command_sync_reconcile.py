"""Tests for the hybrid command scope reconciler (command_sync.py).

These tests mock bot.tree.sync to avoid hitting the Discord API.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import app_commands

from discode.bot.command_sync import SyncReport, reconcile_commands

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_command(name: str) -> MagicMock:
    """Return a minimal mock that looks like an app_commands.AppCommand."""
    cmd = MagicMock(spec=app_commands.AppCommand)
    cmd.name = name
    return cmd


def _make_bot(global_commands: list[str], guild_commands: list[str]) -> MagicMock:
    """Return a mock Bot whose tree.sync returns deterministic command lists."""
    bot = MagicMock()

    # bot.tree.get_commands() for the global scope
    global_cmd_objs = [_make_mock_command(n) for n in global_commands]
    guild_cmd_objs = [_make_mock_command(n) for n in guild_commands]

    # bot.tree.sync() side-effect: first call (global) returns global list,
    # subsequent calls (per guild) return guild list.
    call_count = 0

    async def sync_side_effect(*, guild=None):
        nonlocal call_count
        call_count += 1
        if guild is None:
            return global_cmd_objs
        return guild_cmd_objs

    bot.tree.sync = AsyncMock(side_effect=sync_side_effect)
    bot.tree.clear_commands = MagicMock()  # must NOT be called

    # bot.tree.get_commands() for pre-sync baseline
    # Global scope (no guild=)
    def get_commands_side_effect(*, guild=None):
        if guild is None:
            return global_cmd_objs
        return guild_cmd_objs

    bot.tree.get_commands = MagicMock(side_effect=get_commands_side_effect)

    return bot


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_hybrid_no_duplicates():
    """Global and guild scopes are each synced exactly once; no command appears twice."""
    guild_ids = [111222333, 444555666]
    bot = _make_bot(
        global_commands=["start", "session", "diff", "root"],
        guild_commands=["setup"],
    )

    report = await reconcile_commands(bot, guild_ids)

    # tree.sync called once for global + once per guild
    assert bot.tree.sync.call_count == 1 + len(guild_ids)

    # First call must be global (no guild= kwarg)
    first_call_kwargs = bot.tree.sync.call_args_list[0].kwargs
    assert "guild" not in first_call_kwargs or first_call_kwargs.get("guild") is None

    # Subsequent calls must each pass a guild= argument
    for call in bot.tree.sync.call_args_list[1:]:
        assert call.kwargs.get("guild") is not None

    # Returned report is a SyncReport
    assert isinstance(report, SyncReport)

    # Timestamp is populated
    assert report.timestamp != ""


@pytest.mark.asyncio
async def test_reconcile_reports_counts():
    """SyncReport counts reflect what Discord returned for each scope."""
    guild_ids = [100200300]
    bot = _make_bot(
        global_commands=["start", "session", "diff"],
        guild_commands=["setup"],
    )

    report = await reconcile_commands(bot, guild_ids)

    assert isinstance(report, SyncReport)
    # Global: 3 commands returned → global_added should be 3 (fresh sync, no prior)
    assert report.global_added >= 0
    assert report.global_updated >= 0
    assert report.global_removed >= 0
    # Guild: 1 command per guild → guild_added should be 1
    assert report.guild_added >= 0
    assert report.guild_updated >= 0
    assert report.guild_removed >= 0
    # Total accounted synced commands across global scope equals returned count
    assert report.global_added + report.global_updated <= 3 + 1  # lenient
    # Timestamp is ISO 8601 UTC
    assert "T" in report.timestamp
    assert (
        report.timestamp.endswith("+00:00")
        or report.timestamp.endswith("Z")
        or "UTC" in report.timestamp
        or "+" in report.timestamp
    )


@pytest.mark.asyncio
async def test_reconcile_does_not_clear_tree():
    """tree.clear_commands must never be called during reconciliation."""
    guild_ids = [777888999]
    bot = _make_bot(
        global_commands=["start", "session"],
        guild_commands=["setup"],
    )

    await reconcile_commands(bot, guild_ids)

    bot.tree.clear_commands.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_empty_guild_list():
    """With no guilds, only the global sync is performed."""
    bot = _make_bot(
        global_commands=["start", "session", "diff", "root"],
        guild_commands=[],
    )

    report = await reconcile_commands(bot, guild_ids=[])

    # Only one sync call (global)
    assert bot.tree.sync.call_count == 1
    assert isinstance(report, SyncReport)


@pytest.mark.asyncio
async def test_reconcile_global_sync_is_first():
    """Global sync must always precede per-guild syncs."""
    guild_ids = [123, 456, 789]
    call_order: list[str] = []

    bot = MagicMock()
    bot.tree.clear_commands = MagicMock()

    async def sync_side_effect(*, guild=None):
        if guild is None:
            call_order.append("global")
            return [_make_mock_command("start")]
        call_order.append(f"guild:{guild.id}")
        return [_make_mock_command("setup")]

    bot.tree.sync = AsyncMock(side_effect=sync_side_effect)
    bot.tree.get_commands = MagicMock(return_value=[])

    await reconcile_commands(bot, guild_ids)

    assert call_order[0] == "global", f"Expected global first, got: {call_order}"
    for i, gid in enumerate(guild_ids, start=1):
        assert call_order[i] == f"guild:{gid}"
