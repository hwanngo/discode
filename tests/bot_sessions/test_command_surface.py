"""Verify that /setup command group and subcommands are registered on the bot tree."""

from __future__ import annotations

from discord import app_commands

from discode.bot.main import DiscodeBot


def _get_tree_commands(bot: DiscodeBot) -> dict[str, app_commands.Command | app_commands.Group]:
    """Return a name->command mapping from the bot's command tree."""
    return {cmd.name: cmd for cmd in bot.tree.get_commands()}


def test_setup_group_registered():
    """The bot command tree must contain a top-level 'setup' group."""
    bot = DiscodeBot()
    command_names = {cmd.name for cmd in bot.tree.get_commands()}
    assert "setup" in command_names, f"Expected 'setup' in command tree, got: {command_names}"


def test_setup_subcommands_registered():
    """The setup group must expose init, map, admin-role, and sync subcommands."""
    bot = DiscodeBot()
    commands = _get_tree_commands(bot)
    setup = commands.get("setup")
    assert setup is not None, "setup command not found"
    assert isinstance(setup, app_commands.Group), "setup must be a Group"

    sub_names = {cmd.name for cmd in setup.commands}
    required = {"init", "map", "admin-role", "sync"}
    assert required <= sub_names, (
        f"Missing setup subcommands: {required - sub_names}. Found: {sub_names}"
    )


def test_setup_map_subgroup_registered():
    """setup map must be a subgroup with 'set', 'list', and 'remove' commands."""
    bot = DiscodeBot()
    commands = _get_tree_commands(bot)
    setup = commands["setup"]
    assert isinstance(setup, app_commands.Group)

    sub_lookup = {cmd.name: cmd for cmd in setup.commands}
    map_group = sub_lookup.get("map")
    assert map_group is not None, "setup map subgroup not found"
    assert isinstance(map_group, app_commands.Group), "setup map must be a Group"

    map_sub_names = {cmd.name for cmd in map_group.commands}
    assert {"set", "list", "remove"} <= map_sub_names, (
        f"setup map is missing subcommands. Found: {map_sub_names}"
    )


def test_setup_admin_role_subgroup_registered():
    """setup admin-role must be a subgroup with 'set' and 'list' commands."""
    bot = DiscodeBot()
    commands = _get_tree_commands(bot)
    setup = commands["setup"]
    assert isinstance(setup, app_commands.Group)

    sub_lookup = {cmd.name: cmd for cmd in setup.commands}
    admin_role_group = sub_lookup.get("admin-role")
    assert admin_role_group is not None, "setup admin-role subgroup not found"
    assert isinstance(admin_role_group, app_commands.Group), "setup admin-role must be a Group"

    ar_sub_names = {cmd.name for cmd in admin_role_group.commands}
    assert {"set", "list"} <= ar_sub_names, (
        f"setup admin-role is missing subcommands. Found: {ar_sub_names}"
    )
