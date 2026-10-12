"""Bounded, redacted responses for deferred management commands."""

from __future__ import annotations

import discord

from discode.security.redaction import apply_common_redaction


async def send_deferred_text(interaction: discord.Interaction, text: str) -> None:
    text = apply_common_redaction(text)
    for start in range(0, max(1, len(text)), 1900):
        await interaction.followup.send(
            text[start : start + 1900] or "No results.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
