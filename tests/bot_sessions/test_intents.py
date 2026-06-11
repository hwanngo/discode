from __future__ import annotations

import discord

from discode.bot.intents import check_intents


def test_check_intents_all_present():
    """check_intents returns empty list when all required intents are set."""
    intents = discord.Intents.none()
    intents.guilds = True
    intents.members = True
    intents.guild_messages = True
    assert check_intents(intents) == []


def test_check_intents_missing_guilds():
    """check_intents returns ['GUILDS'] when guilds intent missing."""
    intents = discord.Intents.none()
    intents.guilds = False
    intents.members = True
    intents.guild_messages = True
    missing = check_intents(intents)
    assert missing == ["GUILDS"]


def test_check_intents_all_missing():
    """check_intents returns all 3 names when no intents set."""
    intents = discord.Intents.none()
    missing = check_intents(intents)
    assert "GUILDS" in missing
    assert "GUILD_MEMBERS" in missing
    assert "GUILD_MESSAGES" in missing
    assert len(missing) == 3
