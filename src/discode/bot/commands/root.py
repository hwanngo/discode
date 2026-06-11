from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import CursorResult, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.db.models import AllowedRoot


async def call_canonicalize(control_api_url: str, path: str, auth_token: str) -> dict[str, Any]:
    """POST to control API's /v1/canonicalize_path. Returns response dict."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{control_api_url}/v1/canonicalize_path",
            json={"path": path},
            headers={"Authorization": f"Fernet {auth_token}"},
        )
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result


async def add_root(
    db_factory: async_sessionmaker[AsyncSession],
    guild_id: str,
    user_id: str,
    runner_host_id: str,
    path: str,
    realpath: str,
    created_by: str,
) -> AllowedRoot:
    """Insert an allowed_roots row. Raises ValueError on duplicate."""
    async with db_factory() as session:
        root = AllowedRoot(
            guild_id=guild_id,
            user_id=user_id,
            runner_host_id=runner_host_id,
            path=path,
            realpath=realpath,
            created_by=created_by,
        )
        session.add(root)
        try:
            await session.commit()
            await session.refresh(root)
        except IntegrityError as exc:
            await session.rollback()
            raise ValueError(f"Duplicate allowed root: {realpath!r}") from exc
        return root


async def remove_root(
    db_factory: async_sessionmaker[AsyncSession],
    guild_id: str,
    runner_host_id: str,
    realpath: str,
) -> bool:
    """Delete allowed root. Returns True if deleted, False if not found."""
    async with db_factory() as session:
        cursor: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
            delete(AllowedRoot).where(
                AllowedRoot.guild_id == guild_id,
                AllowedRoot.runner_host_id == runner_host_id,
                AllowedRoot.realpath == realpath,
            )
        )
        await session.commit()
        return bool(cursor.rowcount > 0)


async def list_roots(
    db_factory: async_sessionmaker[AsyncSession],
    guild_id: str,
) -> list[AllowedRoot]:
    """Return all allowed roots for the guild."""
    async with db_factory() as session:
        result = await session.execute(select(AllowedRoot).where(AllowedRoot.guild_id == guild_id))
        return list(result.scalars().all())
