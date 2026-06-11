from __future__ import annotations

import discord

REQUIRED_INTENTS = discord.Intents.none()
REQUIRED_INTENTS.guilds = True
REQUIRED_INTENTS.members = True
REQUIRED_INTENTS.guild_messages = True
OPTIONAL_MESSAGE_CONTENT = discord.Intents.none()
OPTIONAL_MESSAGE_CONTENT.message_content = True


def check_intents(intents: discord.Intents) -> list[str]:
    """Return list of missing required intent names."""
    missing = []
    if not intents.guilds:
        missing.append("GUILDS")
    if not intents.members:
        missing.append("GUILD_MEMBERS")
    if not intents.guild_messages:
        missing.append("GUILD_MESSAGES")
    return missing
