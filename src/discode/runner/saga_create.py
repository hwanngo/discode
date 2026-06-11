"""Runner-owned steps of the create saga.

Steps:
1. Consume runner:jobs:<host_id> entry.
2. Decrypt hook secret ciphertext; keep plaintext in memory only.
3. Lock session row FOR UPDATE.
4. If state != 'creating', ack and return.
5. Re-validate allowed root and denylist.
6. Set status='running', running_at.
7. Insert audit event session.create.running.
8. Enqueue Discord system notice.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid as _uuid
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.queues.outbox import write_outbox
from discode.security.env_scrub import scrub_env
from discode.security.paths import canonicalize
from discode.tools.errors import UnknownTool
from discode.tools.registry import get_adapter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreateJobPayload:
    """Parsed runner.create_session.v1 envelope."""

    session_id: str
    guild_id: str
    owner_id: str
    tool: str
    cwd_realpath: str
    thread_id: str
    hook_secret_ciphertext: str
    idempotency_key: str
    model: str | None = None
    resume_token: str | None = None


class CreateJobError(Exception):
    """Raised when the create job cannot proceed (non-retryable)."""


async def revalidate_cwd(
    db: AsyncSession,
    *,
    cwd: str,
    guild_id: str,
    owner_id: str,
    host_id: str,
) -> tuple[str | None, str | None]:
    """Re-canonicalize ``cwd`` and re-check it against the current allowed roots.

    Shared by the create saga (inside its locked TX) and the resume/recover
    paths (FIX 3), so a stored cwd is never trusted blindly after a symlink
    swap or an allowed-root revocation.

    Returns ``(realpath, None)`` when the cwd is still valid, or
    ``(None, reason)`` when it must be refused.
    """
    from discode.db.models import AllowedRoot

    realpath, deny_reason = canonicalize(cwd)
    if deny_reason is not None:
        return None, deny_reason
    root_result = await db.execute(
        select(AllowedRoot).where(
            AllowedRoot.guild_id == guild_id,
            AllowedRoot.runner_host_id == host_id,
        )
    )
    roots = root_result.scalars().all()
    allowed = any(
        root.user_id in ("", owner_id)
        and (realpath == root.realpath or realpath.startswith(root.realpath + os.sep))
        for root in roots
    )
    if not allowed:
        return None, "no allowed root match"
    return realpath, None


async def run_create_job(
    payload: CreateJobPayload,
    *,
    host_id: str,
    db_factory: async_sessionmaker[AsyncSession],
    session_store: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Execute runner-owned create saga steps 2-8.

    Raises:
        CreateJobError: Non-retryable failures (bad state, root mismatch, decrypt).
        Exception: Transient errors - caller should let XAUTOCLAIM retry.
    """
    from discode.db.models import (
        Event,
    )
    from discode.db.models import (
        Session as SessionModel,
    )

    session_uuid = _uuid.UUID(payload.session_id)

    # Validation result set outside the TX so we can fail in a separate TX after commit.
    _fail_reason: str | None = None
    _fail_msg: str | None = None

    async with db_factory() as db:
        async with db.begin():
            # Step 3: Lock session row FOR UPDATE.
            sess_result = await db.execute(
                select(SessionModel).where(SessionModel.id == session_uuid).with_for_update()
            )
            session = sess_result.scalar_one_or_none()
            if session is None:
                raise CreateJobError(f"session {payload.session_id} not found")

            # Step 4: If state != 'creating', ack and return.
            if session.status != "creating":
                logger.info(
                    "create job: session %s in status=%s; skipping",
                    payload.session_id,
                    session.status,
                )
                return

            # Step 5: Re-validate allowed root and denylist (catches symlink swaps).
            _realpath, deny_reason = await revalidate_cwd(
                db,
                cwd=payload.cwd_realpath,
                guild_id=payload.guild_id,
                owner_id=payload.owner_id,
                host_id=host_id,
            )
            if deny_reason is not None:
                _fail_reason = "host_unavailable"
                _fail_msg = f"cwd rejected for session {payload.session_id}: {deny_reason}"
            # Transaction commits here; session stays in 'creating'.

    # Fail the session in a fresh TX (the lock TX above has already committed).
    if _fail_reason is not None:
        await _fail_and_notify(
            db_factory,
            session_id=payload.session_id,
            guild_id=payload.guild_id,
            thread_id=payload.thread_id,
            status_reason=_fail_reason,
        )
        raise CreateJobError(_fail_msg or _fail_reason)

    # Build scrubbed session environment (docs/architecture.md).
    async with db_factory() as _reg_session:
        try:
            adapter, _tool_def = await get_adapter(_reg_session, payload.tool)
        except UnknownTool as exc:
            logger.warning(
                "create job: unknown tool %s for session %s", exc.name, payload.session_id
            )
            raise CreateJobError(f"unknown or disabled tool: {exc.name}") from exc
    _venv_bin = str(os.path.dirname(sys.executable))
    _tool_paths = ":".join(adapter.extra_paths())
    _base_path = f"{_venv_bin}:/usr/local/bin:/usr/bin:/bin"
    _session_path = f"{_tool_paths}:{_base_path}" if _tool_paths else _base_path
    raw_env = os.environ.copy()
    raw_env.update(
        {
            "PATH": _session_path,
            "HOME": payload.cwd_realpath,
            "LANG": "en_US.UTF-8",
            "TERM": "xterm-256color",
            "DISCORD_AGENT_SESSION_ID": payload.session_id,
            "DAB_SESSION_ID": payload.session_id,
        }
    )
    session_env = scrub_env(
        raw_env,
        extra_allowed=frozenset(adapter.env_allowlist()),
    )

    # Steps 6-8: Set running + audit event + Discord notice - all in one TX.
    async with db_factory() as db:
        async with db.begin():
            update_values: dict[str, Any] = dict(
                status="running",
                host_id=host_id,
                running_at=func.now(),
                hook_secret_ciphertext=payload.hook_secret_ciphertext,
                recovery_checked_at=func.now(),
            )
            if payload.resume_token is not None:
                update_values["tool_resume_token"] = payload.resume_token

            upd_result = await db.execute(
                update(SessionModel)
                .where(
                    SessionModel.id == session_uuid,
                    SessionModel.status == "creating",
                )
                .values(**update_values)
            )

            # FIX 4: if the watchdog/janitor flipped the session out of
            # 'creating' (e.g. to 'failed') between the lock TX and now, the
            # UPDATE matches zero rows. Do NOT claim success — skip the audit
            # event, the outbox notice, and the session_store population.
            affected = cast("CursorResult[Any]", upd_result).rowcount
            if affected != 1:
                logger.warning(
                    "create job: session %s no longer in 'creating' "
                    "(update matched %s rows); not emitting running",
                    payload.session_id,
                    affected,
                )
                return

            db.add(
                Event(
                    session_id=session_uuid,
                    guild_id=payload.guild_id,
                    type="session.create.running",
                    payload={"host_id": host_id},
                )
            )

            await write_outbox(
                db,
                producer="runner",
                stream_key="discord:outbound",
                envelope_type="discord.system_notice.v1",
                payload={
                    "type": "discord.system_notice.v1",
                    "idempotency_key": str(_uuid.uuid4()),
                    "session_id": payload.session_id,
                    "guild_id": payload.guild_id,
                    "thread_id": payload.thread_id,
                    "notice_type": "session_running",
                    "order_after_seq": 0,
                    "message": "",
                },
            )

    # Update in-memory session_store only after the DB transaction has committed
    # to avoid split-brain if the commit fails.
    if session_store is not None:
        from discode.runner.session_store import build_session_store_entry

        session_store[payload.session_id] = build_session_store_entry(
            tool=payload.tool,
            cwd=payload.cwd_realpath,
            env=session_env,
            tool_resume_token=payload.resume_token,
            guild_id=payload.guild_id,
            thread_id=payload.thread_id,
            host_id=host_id,
            status="running",
        )


async def _fail_and_notify(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    guild_id: str,
    thread_id: str,
    status_reason: str,
) -> None:
    """Open a fresh transaction to fail the session after the lock TX has committed."""
    from discode.db.models import Event
    from discode.db.models import Session as SessionModel

    session_uuid = _uuid.UUID(session_id)
    async with db_factory() as db:
        async with db.begin():
            await db.execute(
                update(SessionModel)
                .where(SessionModel.id == session_uuid)
                .values(status="failed", status_reason=status_reason)
            )
            db.add(
                Event(
                    session_id=session_uuid,
                    guild_id=guild_id,
                    type="session.create.failed",
                    payload={"reason": status_reason},
                )
            )
            await write_outbox(
                db,
                producer="runner",
                stream_key="discord:outbound",
                envelope_type="discord.system_notice.v1",
                payload={
                    "type": "discord.system_notice.v1",
                    "idempotency_key": str(_uuid.uuid4()),
                    "session_id": session_id,
                    "guild_id": guild_id,
                    "thread_id": thread_id,
                    "notice_type": "session_create_failed",
                    "order_after_seq": 0,
                    "message": status_reason,
                },
            )


def parse_create_job_payload(fields: dict[str, str]) -> CreateJobPayload:
    """Deserialise a runner.create_session.v1 Redis stream entry into a typed payload."""
    return CreateJobPayload(
        session_id=fields["session_id"],
        guild_id=fields["guild_id"],
        owner_id=fields["owner_id"],
        tool=fields["tool"],
        cwd_realpath=fields["cwd_realpath"],
        thread_id=fields["thread_id"],
        hook_secret_ciphertext=fields["hook_secret_ciphertext"],
        idempotency_key=fields["idempotency_key"],
        model=fields.get("model"),
        resume_token=fields.get("resume_token"),
    )
