"""Validate the runner workspace before creating a Discord thread."""

from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.bot.diff_service import _runner_control_auth_headers, _runner_control_base_url
from discode.db.models import AllowedRoot, RunnerHost
from discode.security.paths import is_within_root


async def validate_workspace(
    factory: async_sessionmaker[AsyncSession],
    *,
    cwd: str,
    guild_id: str,
    owner_id: str,
    host_id: str,
) -> str:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(
                f"{_runner_control_base_url()}/v1/canonicalize_path",
                json={"path": cwd},
                headers=_runner_control_auth_headers(),
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ValueError("Runner workspace validation is unavailable; retry shortly") from exc
    if not isinstance(data, dict) or not isinstance(data.get("realpath"), str):
        raise ValueError("Invalid workspace validation response")
    if data.get("denylist_verdict") != "allow" or data.get("is_dir") is not True:
        raise ValueError("Working directory is unavailable or denied")
    path = str(data["realpath"])
    async with factory() as db:
        host = await db.get(RunnerHost, host_id)
        if host is None or host.status != "online":
            raise ValueError("Runner is not online")
        roots = (
            await db.scalars(
                select(AllowedRoot).where(
                    AllowedRoot.guild_id == guild_id,
                    AllowedRoot.runner_host_id == host_id,
                    AllowedRoot.user_id.in_(["", owner_id]),
                )
            )
        ).all()
        if not any(is_within_root(path, root.realpath) for root in roots):
            raise ValueError("Register an allowed project root with /root add first")
    return path
