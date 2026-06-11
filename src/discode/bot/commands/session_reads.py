from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


async def get_sessions_for_user(
    db_factory: async_sessionmaker[AsyncSession],
    guild_id: str,
    user_id: str,
    include_all: bool = False,
) -> list[dict[str, object]]:
    """
    List sessions for a user.
    - Default: sessions where user is a member and status in (running, idle, archived, resuming).
    - include_all=True: all sessions in the guild (requires admin role).

    Returns list of dicts with keys: session_id, name, tool, status, cwd, created_at, owner_id.
    """
    async with db_factory() as db:
        if include_all:
            result = await db.execute(
                text("""
                    SELECT s.id, s.name, s.tool, s.status, s.cwd, s.created_at, s.owner_id
                    FROM sessions s
                    WHERE s.guild_id = :gid
                      AND s.status NOT IN ('stopped', 'failed')
                    ORDER BY s.created_at DESC
                    LIMIT 50
                """),
                {"gid": guild_id},
            )
        else:
            result = await db.execute(
                text("""
                    SELECT s.id, s.name, s.tool, s.status, s.cwd, s.created_at, s.owner_id
                    FROM sessions s
                    JOIN session_members sm ON sm.session_id = s.id
                    WHERE s.guild_id = :gid
                      AND sm.user_id = :uid
                      AND s.status NOT IN ('stopped', 'failed')
                    ORDER BY s.created_at DESC
                    LIMIT 25
                """),
                {"gid": guild_id, "uid": user_id},
            )
        rows = result.fetchall()
        return [
            {
                "session_id": str(row[0]),
                "name": row[1],
                "tool": row[2],
                "status": row[3],
                "cwd": row[4],
                "created_at": row[5],
                "owner_id": row[6],
            }
            for row in rows
        ]


async def get_last_session(
    db_factory: async_sessionmaker[AsyncSession],
    guild_id: str,
    user_id: str,
) -> dict[str, object] | None:
    """Get the user's most recently created session."""
    async with db_factory() as db:
        result = await db.execute(
            text("""
                SELECT s.id, s.name, s.tool, s.status, s.thread_id
                FROM sessions s
                JOIN session_members sm ON sm.session_id = s.id
                WHERE s.guild_id = :gid AND sm.user_id = :uid
                ORDER BY s.created_at DESC
                LIMIT 1
            """),
            {"gid": guild_id, "uid": user_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return {
            "session_id": str(row[0]),
            "name": row[1],
            "tool": row[2],
            "status": row[3],
            "thread_id": row[4],
        }
