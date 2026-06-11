"""Tests for /help slash command (auto-discovery + render + handler)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import app_commands


def _make_leaf(name: str, description: str = "", params: list[tuple[str, bool]] | None = None):
    """Build a fake app_commands.Command leaf for testing."""
    cmd = MagicMock(spec=app_commands.Command)
    cmd.name = name
    cmd.description = description
    fake_params = []
    for pname, required in params or []:
        p = MagicMock()
        p.name = pname
        p.required = required
        fake_params.append(p)
    cmd.parameters = fake_params
    return cmd


def _make_group(name: str, description: str, children: list):
    """Build a fake app_commands.Group with the given children."""
    grp = MagicMock(spec=app_commands.Group)
    grp.name = name
    grp.description = description
    grp.commands = children
    return grp


def _make_tree(top_level: list):
    tree = MagicMock()
    tree.get_commands = MagicMock(return_value=top_level)
    return tree


def test_walk_commands_flattens_top_level_leaves():
    from discode.bot.main import _walk_commands

    leaves = [
        _make_leaf("start", "Start a session", [("tool", True), ("name", True), ("cwd", True)]),
        _make_leaf("help", "Show this help"),
    ]
    result = _walk_commands(_make_tree(leaves))
    paths = [entry["path"] for entry in result]
    assert ("start",) in paths
    assert ("help",) in paths


def test_walk_commands_recurses_into_groups():
    from discode.bot.main import _walk_commands

    add = _make_leaf("add", "Allow a directory", [("path", True), ("alias", False)])
    set_alias = _make_leaf("set", "Register an alias", [("alias", True), ("path", True)])
    list_alias = _make_leaf("list", "List aliases")
    alias_group = _make_group("alias", "Manage aliases", [set_alias, list_alias])
    root_group = _make_group("root", "Manage roots", [add, alias_group])

    result = _walk_commands(_make_tree([root_group]))
    paths = [entry["path"] for entry in result]
    assert ("root", "add") in paths
    assert ("root", "alias", "set") in paths
    assert ("root", "alias", "list") in paths


def test_walk_commands_captures_args_required_optional():
    from discode.bot.main import _walk_commands

    cmd = _make_leaf("invite", "Invite a user", [("user", True), ("session", False)])
    result = _walk_commands(_make_tree([cmd]))
    args = result[0]["args"]
    assert args == "<user> [session]"


def test_walk_commands_captures_description():
    from discode.bot.main import _walk_commands

    cmd = _make_leaf("send", "Inject text into a session")
    result = _walk_commands(_make_tree([cmd]))
    assert result[0]["description"] == "Inject text into a session"


def test_render_help_includes_command_names():
    from discode.bot.main import _render_help_text

    leaves = [
        _make_leaf("start", "Start a session", [("tool", True)]),
        _make_leaf("help", "Show this help"),
    ]
    chunks = _render_help_text(_make_tree(leaves))
    assert len(chunks) == 1
    text = chunks[0]
    assert "/start" in text
    assert "/help" in text


def test_render_help_renders_groups_with_indented_leaves():
    from discode.bot.main import _render_help_text

    list_cmd = _make_leaf("list", "List registered tools")
    register = _make_leaf("register", "Register a new tool", [("name", True), ("config", True)])
    tool_group = _make_group("tool", "Tool management", [list_cmd, register])

    chunks = _render_help_text(_make_tree([tool_group]))
    text = chunks[0]
    assert "/tool" in text
    assert "\n  list" in text
    assert "\n  register" in text


def test_render_help_appends_perm_tags():
    from discode.bot.main import _render_help_text

    register = _make_leaf("register", "Register a new tool", [("name", True), ("config", True)])
    tool_group = _make_group("tool", "Tool management", [register])

    chunks = _render_help_text(_make_tree([tool_group]))
    text = chunks[0]
    assert "[admin]" in text


def test_render_help_no_tag_for_unmapped_commands():
    from discode.bot.main import _render_help_text

    cmd = _make_leaf("totally_new_thing", "Brand new")
    chunks = _render_help_text(_make_tree([cmd]))
    text = chunks[0]
    assert "[admin]" not in text
    assert "[owner+]" not in text
    assert "[member+]" not in text


def test_render_help_required_vs_optional_args():
    from discode.bot.main import _render_help_text

    cmd = _make_leaf("invite", "Invite a user", [("user", True), ("session", False)])
    chunks = _render_help_text(_make_tree([cmd]))
    text = chunks[0]
    assert "<user>" in text
    assert "[session]" in text


def test_render_help_truncates_long_descriptions():
    from discode.bot.main import _render_help_text

    long_desc = "x" * 200
    cmd = _make_leaf("foo", long_desc)
    chunks = _render_help_text(_make_tree([cmd]))
    text = chunks[0]
    assert "x" * 59 + "…" in text
    assert "x" * 60 not in text


def test_render_help_chunks_when_output_exceeds_limit():
    from discode.bot.main import _render_help_text

    big_groups = []
    for g in range(8):
        leaves = [
            _make_leaf(f"cmd{i}", f"Description for cmd{i}", [("a", True), ("b", False)])
            for i in range(8)
        ]
        big_groups.append(_make_group(f"group{g}", "g", leaves))
    chunks = _render_help_text(_make_tree(big_groups))
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 1950, f"chunk too big: {len(chunk)} chars"


def test_render_help_wraps_chunks_in_code_blocks():
    from discode.bot.main import _render_help_text

    cmd = _make_leaf("start", "Start", [("tool", True)])
    chunks = _render_help_text(_make_tree([cmd]))
    assert chunks[0].startswith("```")
    assert chunks[0].rstrip().endswith("```")


@pytest.mark.asyncio
async def test_help_command_responds_ephemerally():
    """The /help callback posts an ephemeral message containing the rendered help."""
    from discode.bot.main import build_help_command

    help_cmd = build_help_command()

    interaction = MagicMock()
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.client = MagicMock()
    interaction.client.tree = _make_tree(
        [
            _make_leaf("start", "Start a session", [("tool", True)]),
            _make_leaf("help", "Show this help"),
        ]
    )

    await help_cmd.callback(interaction)

    interaction.response.send_message.assert_called_once()
    kwargs = interaction.response.send_message.call_args.kwargs
    assert kwargs.get("ephemeral") is True
    msg = interaction.response.send_message.call_args.args[0]
    assert "/start" in msg
    assert "/help" in msg


@pytest.mark.asyncio
async def test_help_command_chunks_via_followup():
    """When rendered output is multi-chunk, the first chunk goes via response,
    subsequent chunks via followup.send."""
    from discode.bot.main import build_help_command

    help_cmd = build_help_command()

    big_groups = []
    for g in range(8):
        leaves = [
            _make_leaf(f"cmd{i}", f"Description for cmd{i}", [("a", True), ("b", False)])
            for i in range(8)
        ]
        big_groups.append(_make_group(f"group{g}", "g", leaves))

    interaction = MagicMock()
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.client = MagicMock()
    interaction.client.tree = _make_tree(big_groups)

    await help_cmd.callback(interaction)

    interaction.response.send_message.assert_called_once()
    assert interaction.followup.send.await_count >= 1
    first_kwargs = interaction.response.send_message.call_args.kwargs
    assert first_kwargs.get("ephemeral") is True
    for call in interaction.followup.send.await_args_list:
        assert call.kwargs.get("ephemeral") is True
