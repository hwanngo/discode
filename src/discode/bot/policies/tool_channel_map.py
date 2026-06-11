from __future__ import annotations

import datetime as dt
from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from discode.db.models import GuildToolChannelMap


async def set_tool_channel_map(
    session: AsyncSession,
    guild_id: str,
    tool: str,
    channel_id: str,
    updated_by: str,
) -> GuildToolChannelMap:
    """Upsert a (guild_id, tool) -> channel_id mapping.

    Uses PostgreSQL INSERT ... ON CONFLICT DO UPDATE so a second call
    with the same (guild_id, tool) updates channel_id and updated_by.
    Returns the live ORM row (re-fetched after flush).
    """
    now = datetime.now(dt.UTC)
    stmt = (
        pg_insert(GuildToolChannelMap)
        .values(
            guild_id=guild_id,
            tool=tool,
            channel_id=channel_id,
            updated_by=updated_by,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            constraint="guild_tool_channel_map_guild_tool_uq",
            set_={
                "channel_id": channel_id,
                "updated_by": updated_by,
                "updated_at": now,
            },
        )
    )
    await session.execute(stmt)
    await session.flush()

    result = await session.execute(
        select(GuildToolChannelMap).where(
            GuildToolChannelMap.guild_id == guild_id,
            GuildToolChannelMap.tool == tool,
        )
    )
    return result.scalars().one()


async def get_tool_channel_map(
    session: AsyncSession,
    guild_id: str,
    tool: str,
) -> GuildToolChannelMap | None:
    """Return the mapping row for (guild_id, tool) or None if absent."""
    result = await session.execute(
        select(GuildToolChannelMap).where(
            GuildToolChannelMap.guild_id == guild_id,
            GuildToolChannelMap.tool == tool,
        )
    )
    return result.scalars().first()


async def resolve_tool_channel(
    session: AsyncSession,
    guild_id: str,
    tool: str,
) -> str | None:
    """Return the channel_id for (guild_id, tool), or None if no mapping exists."""
    row = await get_tool_channel_map(session, guild_id, tool)
    return row.channel_id if row is not None else None


async def list_tool_channel_maps(
    session: AsyncSession,
    guild_id: str,
) -> list[GuildToolChannelMap]:
    """Return all tool-channel mappings for a guild."""
    result = await session.execute(
        select(GuildToolChannelMap).where(GuildToolChannelMap.guild_id == guild_id)
    )
    return list(result.scalars().all())


async def remove_tool_channel_map(
    session: AsyncSession,
    guild_id: str,
    tool: str,
) -> bool:
    """Delete the (guild_id, tool) mapping. Returns True if a row was deleted."""
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            delete(GuildToolChannelMap).where(
                GuildToolChannelMap.guild_id == guild_id,
                GuildToolChannelMap.tool == tool,
            )
        ),
    )
    await session.flush()
    return result.rowcount > 0
