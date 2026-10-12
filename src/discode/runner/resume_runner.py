from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


async def run_resume_job(
    payload: dict[str, Any],
    *,
    host_id: str,
    session_store: dict[str, dict[str, Any]],
    db_factory: async_sessionmaker[AsyncSession],
) -> bool:
    """Runner-side resume: rehydrate the in-memory store and mark running.

    Returns True on success, False on failure (will emit terminal notice).
    """
    from discode.runner.saga_create import revalidate_cwd
    from discode.runner.session_store import build_session_store_entry

    session_id = payload.get("session_id", "")
    guild_id = payload.get("guild_id", "")
    thread_id = payload.get("thread_id", "")

    async with db_factory() as db:
        result = await db.execute(
            text("""
                SELECT tool, cwd_realpath, tool_resume_token, owner_id, guild_id,
                       lifecycle_version, thread_id
                FROM sessions
                WHERE id = :sid AND host_id = :hid AND status = 'resuming'
            """),
            {"sid": session_id, "hid": host_id},
        )
        row = result.fetchone()

    if row is None:
        return False

    tool, cwd, resume_token, owner_id, db_guild_id, version, db_thread_id = row
    if payload.get("lifecycle_version") != version:
        return False
    thread_id = db_thread_id or ""
    guild_id = guild_id or (db_guild_id or "")

    # FIX 3: re-canonicalize + re-check the stored cwd against current allowed
    # roots before rehydrating — a symlink swap or revoked root must not run.
    async with db_factory() as db:
        realpath, deny_reason = await revalidate_cwd(
            db,
            cwd=cwd or "",
            guild_id=db_guild_id or guild_id,
            owner_id=owner_id or "",
            host_id=host_id,
        )
    if deny_reason is not None:
        logger.warning(
            "resume refused for session %s: cwd revalidation failed: %s",
            session_id,
            deny_reason,
        )
        async with db_factory() as db:
            async with db.begin():
                failed = await db.execute(
                    text(
                        "UPDATE sessions SET status='failed', status_reason='cwd_invalid'"
                        " WHERE id=:sid AND lifecycle_version=:version AND status='resuming'"
                        " RETURNING id"
                    ),
                    {"sid": session_id, "version": version},
                )
                if failed.first() is not None and thread_id:
                    from discode.queues.outbox import write_outbox

                    await write_outbox(
                        db,
                        producer="runner",
                        stream_key="discord:outbound",
                        envelope_type="discord.terminal_notice.v1",
                        payload={
                            "type": "discord.terminal_notice.v1",
                            "idempotency_key": str(uuid.uuid4()),
                            "session_id": session_id,
                            "guild_id": guild_id,
                            "thread_id": thread_id,
                            "message": (
                                "Session resume failed: the workspace is no longer an "
                                "allowed root on the runner."
                            ),
                            "then_archive": True,
                        },
                    )
        return False
    cwd = realpath
    if resume_token is None:
        from discode.tools.registry import get_adapter

        async with db_factory() as db:
            adapter, _ = await get_adapter(db, tool)
            resume_token = adapter.mint_initial_token()

    try:
        async with db_factory() as db:
            async with db.begin():
                updated = await db.execute(
                    text(
                        "UPDATE sessions SET status='running', last_activity_at=now(), "
                        "tool_resume_token=:token "
                        "WHERE id=:sid "
                        "AND lifecycle_version=:version AND status='resuming' RETURNING id"
                    ),
                    {"sid": session_id, "version": version, "token": resume_token},
                )
                if updated.first() is None:
                    return False

        session_store[session_id] = build_session_store_entry(
            tool=tool,
            cwd=cwd or "",
            env={},
            tool_resume_token=resume_token,
            guild_id=guild_id,
            thread_id=thread_id,
            host_id=host_id,
            status="running",
        )
        return True

    except Exception:
        logger.exception("resume failed for session %s", session_id)
        async with db_factory() as db:
            async with db.begin():
                from discode.queues.outbox import write_outbox

                await db.execute(
                    text(
                        "UPDATE sessions SET status='orphaned', status_reason='runner_lost'"
                        " WHERE id=:sid AND lifecycle_version=:version AND status='resuming'"
                    ),
                    {"sid": session_id, "version": version},
                )
                await write_outbox(
                    db,
                    producer="runner",
                    stream_key="discord:outbound",
                    envelope_type="discord.terminal_notice.v1",
                    payload={
                        "type": "discord.terminal_notice.v1",
                        "idempotency_key": str(uuid.uuid4()),
                        "session_id": session_id,
                        "guild_id": guild_id,
                        "thread_id": thread_id,
                        "message": "Session resume failed.",
                        "then_archive": True,
                    },
                )
        return False
