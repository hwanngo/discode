from __future__ import annotations

from typing import Any


def build_session_store_entry(
    *,
    tool: str,
    cwd: str,
    env: dict[str, str],
    tool_resume_token: str | None,
    guild_id: str | None,
    thread_id: str | None,
    host_id: str,
    status: str,
    channel_id: str = "",
) -> dict[str, Any]:
    """In-memory entry shared by saga_create, saga_resume, and resume_runner.

    The runner spawns one short-lived tool subprocess per turn; ``tool``,
    ``cwd``, ``env``, and ``tool_resume_token`` are everything the per-turn
    builder needs to construct an ``ExecSession``.
    """
    return {
        "tool": tool,
        "cwd": cwd,
        "env": env,
        "tool_resume_token": tool_resume_token,
        "guild_id": guild_id,
        "thread_id": thread_id,
        "host_id": host_id,
        "status": status,
        "channel_id": channel_id,
    }
