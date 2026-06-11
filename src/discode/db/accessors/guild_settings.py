from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_setting(session: AsyncSession, guild_id: str, key: str, default: Any = None) -> Any:
    """Get a typed value from guild_settings JSONB by dot-separated key path."""
    result = await session.execute(
        text("SELECT settings FROM guild_settings WHERE guild_id = :guild_id"),
        {"guild_id": guild_id},
    )
    row = result.fetchone()
    if row is None or row[0] is None:
        return default
    settings: dict[str, Any] = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    raw = settings.get(key)
    if raw is None:
        return default
    return raw


async def set_setting(session: AsyncSession, guild_id: str, key: str, value: Any) -> None:
    """Upsert a single key in guild_settings JSON atomically.

    Performs an atomic read-modify-write in one statement by merging the new key
    into the existing blob via jsonb concatenation (``||``). This avoids the
    lost-update race that a Python-side read/mutate/write would have: two
    concurrent writers of *different* keys would otherwise clobber each other.
    The existing blob is preserved and only ``key`` is replaced.
    """
    # Wrap the single key/value as a JSON object string; merge server-side so
    # concurrent writers of different keys do not clobber one another.
    patch = json.dumps({key: value})

    await session.execute(
        text(
            """
            INSERT INTO guild_settings (guild_id, settings)
            VALUES (:guild_id, cast(:patch as json))
            ON CONFLICT (guild_id) DO UPDATE
            SET settings = cast(
                COALESCE(guild_settings.settings::jsonb, '{}'::jsonb)
                || cast(:patch as jsonb)
                as json
            )
            """
        ),
        {"guild_id": guild_id, "patch": patch},
    )
    await session.flush()
