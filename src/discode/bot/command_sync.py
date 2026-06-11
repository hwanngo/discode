"""Hybrid command scope reconciler.

Stable commands (session, diff, root, start) are registered globally.
Fast-moving admin/setup commands are registered per-guild.

The reconciler syncs each scope idempotently — it never clears the command
tree, relying on Discord's deduplication by name+scope.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

# Commands that belong in the global scope (stable, slow-moving)
GLOBAL_COMMAND_NAMES: frozenset[str] = frozenset({"session", "diff", "root", "start"})

# Commands that belong in the guild scope (fast-moving admin/setup)
GUILD_COMMAND_NAMES: frozenset[str] = frozenset({"setup"})


@dataclass
class SyncReport:
    global_added: int = 0
    global_updated: int = 0
    global_removed: int = 0
    guild_added: int = 0
    guild_updated: int = 0
    guild_removed: int = 0
    timestamp: str = field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat())


def _compute_delta(
    before_names: set[str],
    after_names: set[str],
) -> tuple[int, int, int]:
    """Return (added, unchanged, removed) counts given name sets.

    Since Discord's sync API is idempotent and returns the authoritative
    post-sync list, we treat:
    - Commands in after but not before → added
    - Commands in both before and after → unchanged (Discord doesn't report
      whether the payload changed, so "updated" would be misleading)
    - Commands in before but not after → removed
    """
    added = len(after_names - before_names)
    updated = len(after_names & before_names)
    removed = len(before_names - after_names)
    return added, updated, removed


async def reconcile_commands(
    bot: commands.Bot,
    guild_ids: list[int],
) -> SyncReport:
    """Reconcile global and guild-scoped command registrations.

    Steps:
    1. Snapshot pre-sync global command names from the local tree.
    2. Sync global scope (no guild= arg) — Discord deduplicates by name.
    3. Snapshot pre-sync guild command names from the local tree.
    4. For each guild: sync guild scope.
    5. Accumulate deltas across all guild syncs.
    6. Return a SyncReport with per-scope counts and a UTC timestamp.

    Key constraint: tree.clear_commands is NEVER called.
    """
    # --- Step 1: baseline for global scope ---
    before_global: set[str] = {cmd.name for cmd in bot.tree.get_commands(guild=None)}

    # --- Step 2: sync global ---
    logger.info("command_sync: syncing global commands")
    global_result = await bot.tree.sync()
    after_global: set[str] = {cmd.name for cmd in global_result}
    g_added, g_updated, g_removed = _compute_delta(before_global, after_global)
    logger.info(
        "command_sync: global sync complete",
        extra={"added": g_added, "updated": g_updated, "removed": g_removed},
    )

    # --- Steps 3-5: per-guild sync ---
    total_guild_added = 0
    total_guild_updated = 0
    total_guild_removed = 0

    for guild_id in guild_ids:
        guild_obj = discord.Object(id=guild_id)
        before_guild: set[str] = {cmd.name for cmd in bot.tree.get_commands(guild=guild_obj)}

        logger.info("command_sync: syncing guild %s", guild_id)
        try:
            guild_result = await bot.tree.sync(guild=guild_obj)
        except Exception as exc:
            logger.warning("guild sync failed for %s: %s", guild_id, exc)
            continue
        after_guild: set[str] = {cmd.name for cmd in guild_result}

        ga, gu, gr = _compute_delta(before_guild, after_guild)
        total_guild_added += ga
        total_guild_updated += gu
        total_guild_removed += gr
        logger.info(
            "command_sync: guild %s sync complete",
            guild_id,
            extra={"added": ga, "updated": gu, "removed": gr},
        )

    return SyncReport(
        global_added=g_added,
        global_updated=g_updated,
        global_removed=g_removed,
        guild_added=total_guild_added,
        guild_updated=total_guild_updated,
        guild_removed=total_guild_removed,
        timestamp=dt.datetime.now(dt.UTC).isoformat(),
    )
