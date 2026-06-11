"""Session service helpers for the bot layer.

Provides higher-level operations that sit above the raw sagas, such as
atomically binding a Discord thread_id to a session record + its queued
outbox payload, and helpers for resolving sessions from incoming Discord
thread messages.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.db.models import RedisOutbox, SessionMember
from discode.db.models import Session as SessionModel
from discode.queues.outbox import write_outbox

logger = logging.getLogger(__name__)


@dataclass
class BindThreadResult:
    """Result of a bind_session_thread call."""

    success: bool
    session_id: str
    thread_id: str
    # Non-None only when success=False; describes the failure reason.
    status_reason: str | None = None
    error: str | None = None


async def bind_session_thread(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    thread_id: str,
) -> BindThreadResult:
    """Transactionally bind a Discord thread_id to the session row and
    update the queued ``runner.create_session.v1`` outbox payload.

    Returns a :class:`BindThreadResult`.  On failure, ``success`` is
    ``False`` and ``status_reason`` is ``"thread_bind_failed"`` — the
    caller should mark the session with that reason and enqueue a repair
    rather than surfacing an error to the Discord user.

    Atomicity guarantee
    -------------------
    Both the ``sessions.thread_id`` column update and the
    ``redis_outbox.payload`` JSON patch happen inside a single
    ``BEGIN`` / ``COMMIT``.  If any step fails, the whole transaction
    is rolled back so the two stores never diverge.
    """
    session_uuid = uuid.UUID(session_id)

    try:
        async with db_factory() as db:
            async with db.begin():
                # 1. Load the session row (with a row-level lock to prevent
                #    concurrent double-binds).
                session_row = await db.get(
                    SessionModel,
                    session_uuid,
                    with_for_update=True,
                )
                if session_row is None:
                    logger.warning(
                        "bind_session_thread: session not found",
                        extra={"session_id": session_id},
                    )
                    return BindThreadResult(
                        success=False,
                        session_id=session_id,
                        thread_id=thread_id,
                        status_reason="thread_bind_failed",
                        error="session_not_found",
                    )

                # 2. Guard against re-binding an already-bound session.
                if session_row.thread_id == thread_id:
                    # Idempotent: same thread already bound — treat as success.
                    return BindThreadResult(
                        success=True,
                        session_id=session_id,
                        thread_id=thread_id,
                    )
                if session_row.thread_id is not None:
                    # Different thread — refuse to overwrite.
                    logger.warning(
                        "bind_session_thread: session already bound to a different thread",
                        extra={
                            "session_id": session_id,
                            "existing_thread_id": session_row.thread_id,
                            "new_thread_id": thread_id,
                        },
                    )
                    return BindThreadResult(
                        success=False,
                        session_id=session_id,
                        thread_id=thread_id,
                        status_reason="already_bound",
                    )

                # 3. Update session.thread_id (only reached when thread_id is None).
                session_row.thread_id = thread_id

                # 4. Fetch the queued outbox row (not yet published) and
                #    patch its JSON payload in Python, then persist.
                outbox_result = await db.execute(
                    sa.select(RedisOutbox)
                    .where(
                        RedisOutbox.envelope_type == "runner.create_session.v1",
                        RedisOutbox.published_at.is_(None),
                        sa.func.json_extract_path_text(RedisOutbox.payload, "session_id")
                        == session_id,
                    )
                    .with_for_update()
                )
                outbox_row = outbox_result.scalar_one_or_none()

                if outbox_row is not None:
                    # Build a new dict so SQLAlchemy detects the mutation
                    # (JSON columns need a new object reference to mark dirty).
                    new_payload = dict(outbox_row.payload)
                    new_payload["thread_id"] = thread_id
                    outbox_row.payload = new_payload

        logger.info(
            "bind_session_thread: bound",
            extra={"session_id": session_id, "thread_id": thread_id},
        )
        return BindThreadResult(
            success=True,
            session_id=session_id,
            thread_id=thread_id,
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "bind_session_thread: failed",
            extra={"session_id": session_id, "thread_id": thread_id},
        )
        return BindThreadResult(
            success=False,
            session_id=session_id,
            thread_id=thread_id,
            status_reason="thread_bind_failed",
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Thread message routing helpers
# ---------------------------------------------------------------------------


async def resolve_session_for_thread(
    db: AsyncSession,
    *,
    thread_id: str,
    guild_id: str,
) -> SessionModel | None:
    """Return the active session bound to *thread_id* in *guild_id*, or None.

    Only returns sessions with a non-null ``thread_id`` that matches and whose
    guild scoping also matches.  Inactive (stopped/archived/failed) sessions
    are intentionally included — the caller (``on_message``) decides whether to
    act on them; silently ignoring is fine for now since runners won't accept
    input for inactive sessions anyway.
    """
    result = await db.execute(
        sa.select(SessionModel).where(
            SessionModel.thread_id == thread_id,
            SessionModel.guild_id == guild_id,
        )
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Session resolution helpers
# ---------------------------------------------------------------------------


async def resolve_session_target(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    guild_id: str,
    user_id: str,
    session_ref: str | None,
) -> dict[str, str] | None:
    """Resolve *session_ref* to a session accessible by *user_id* in *guild_id*.

    - UUID string → look up by id, restricted to sessions the user owns or is a member of.
    - Name string → look up by name, returning the user's most recent matching session.
    - None → return the user's most recently created session in the guild.

    Returns ``{"session_id": str, "name": str}`` or ``None`` if not found.
    """
    member_subq = sa.select(SessionMember.session_id).where(SessionMember.user_id == user_id)
    base = (
        sa.select(SessionModel)
        .where(
            SessionModel.guild_id == guild_id,
            sa.or_(
                SessionModel.owner_id == user_id,
                SessionModel.id.in_(member_subq),
            ),
        )
        .order_by(SessionModel.created_at.desc())
    )

    if session_ref is None:
        stmt = base.limit(1)
    else:
        try:
            session_uuid = uuid.UUID(session_ref)
            stmt = base.where(SessionModel.id == session_uuid).limit(1)
        except ValueError:
            stmt = base.where(SessionModel.name == session_ref).limit(1)

    async with db_factory() as db:
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()

    if row is None:
        return None
    return {
        "session_id": str(row.id),
        "name": row.name,
        "cwd": row.cwd,
        "thread_id": row.thread_id,
        "host_id": row.host_id,
        "owner_id": row.owner_id,
    }


async def send_input_for_session(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    guild_id: str,
    thread_id: str,
    host_id: str,
    owner_id: str,
    text_content: str,
    enable_message_content: bool = False,
) -> str:
    """Inject `text_content` as input into the session, as if a user typed it
    in the thread. Returns the idempotency_key.

    Caller is responsible for permission checks.
    """
    from discode.bot.sagas.input import run_input_saga as _live_input_saga

    return await _live_input_saga(
        db_factory,
        session_id=session_id,
        guild_id=guild_id,
        thread_id=thread_id,
        host_id=host_id,
        owner_id=owner_id,
        text_content=text_content,
        enable_message_content=enable_message_content,
    )


async def run_stop_saga(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    guild_id: str,
    requested_by_id: str,
    host_id: str | None = None,
) -> bool:
    import uuid as _uuid

    from sqlalchemy import func, update

    from discode.db.models import Event
    from discode.db.models import Session as SessionModel

    session_uuid = _uuid.UUID(session_id) if isinstance(session_id, str) else session_id
    async with db_factory() as db:
        async with db.begin():
            # Load the row first so we can derive host_id for the runner envelope
            # when the caller did not supply it.
            sess = await db.get(SessionModel, session_uuid)
            if (
                sess is None
                or sess.guild_id != guild_id
                or sess.status not in ("running", "idle")
            ):
                return False

            result = await db.execute(
                update(SessionModel)
                .where(
                    SessionModel.id == session_uuid,
                    SessionModel.guild_id == guild_id,
                    SessionModel.status.in_(["running", "idle"]),
                )
                .values(status="stopped", stopped_at=func.now())
            )
            if (result.rowcount or 0) == 0:
                return False
            db.add(
                Event(
                    idempotency_key=str(_uuid.uuid4()),
                    guild_id=guild_id,
                    session_id=session_uuid,
                    actor_id=requested_by_id,
                    type="session.stop.requested",
                    payload={},
                )
            )

            # Tell the runner to drop its in-memory session_store entry. Without
            # this the entry leaks forever. Same transaction as the state change.
            target_host = host_id or sess.host_id or ""
            await write_outbox(
                db,
                producer="bot",
                stream_key=f"runner:jobs:{target_host}",
                envelope_type="runner.stop_session.v1",
                payload={
                    "type": "runner.stop_session.v1",
                    "idempotency_key": str(_uuid.uuid4()),
                    "session_id": str(session_id),
                    "guild_id": guild_id,
                    "host_id": target_host,
                },
            )
    return True


async def stop_session(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    guild_id: str,
    requested_by_id: str,
    host_id: str | None = None,
) -> bool:
    return await run_stop_saga(
        db_factory,
        session_id=session_id,
        guild_id=guild_id,
        requested_by_id=requested_by_id,
        host_id=host_id,
    )


async def run_archive_saga(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    guild_id: str,
) -> bool:
    import uuid as _uuid

    from sqlalchemy import func, update

    from discode.db.models import Event
    from discode.db.models import Session as SessionModel

    session_uuid = _uuid.UUID(session_id) if isinstance(session_id, str) else session_id
    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                update(SessionModel)
                .where(
                    SessionModel.id == session_uuid,
                    SessionModel.guild_id == guild_id,
                    SessionModel.status.in_(["running", "idle", "stopped"]),
                )
                .values(status="archived", archived_at=func.now())
            )
            if (result.rowcount or 0) == 0:
                return False
            db.add(
                Event(
                    idempotency_key=str(_uuid.uuid4()),
                    guild_id=guild_id,
                    session_id=session_uuid,
                    actor_id=None,
                    type="session.archive.requested",
                    payload={},
                )
            )
    return True


async def archive_session(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    guild_id: str,
) -> bool:
    return await run_archive_saga(db_factory, session_id=session_id, guild_id=guild_id)


async def run_resume_saga(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    guild_id: str,
    host_id: str,
) -> bool:
    import uuid as _uuid

    from sqlalchemy import update

    from discode.db.models import Event
    from discode.db.models import Session as SessionModel

    session_uuid = _uuid.UUID(session_id) if isinstance(session_id, str) else session_id
    async with db_factory() as db:
        async with db.begin():
            sess = await db.get(SessionModel, session_uuid)
            if sess is None or sess.guild_id != guild_id or sess.status != "archived":
                return False

            # Move to 'resuming' (NOT 'running'): the runner's resume consumer
            # selects WHERE status='resuming' before rehydrating its in-memory
            # session_store and then flips the row to 'running' itself. If we set
            # 'running' here the runner would never rehydrate and input would be
            # dropped.
            result = await db.execute(
                update(SessionModel)
                .where(
                    SessionModel.id == session_uuid,
                    SessionModel.guild_id == guild_id,
                    SessionModel.status == "archived",
                )
                .values(status="resuming", archived_at=None)
            )
            if (result.rowcount or 0) == 0:
                return False
            db.add(
                Event(
                    idempotency_key=str(_uuid.uuid4()),
                    guild_id=guild_id,
                    session_id=session_uuid,
                    actor_id=None,
                    type="session.resume.requested",
                    payload={},
                )
            )

            target_host = host_id or sess.host_id or ""
            await write_outbox(
                db,
                producer="bot",
                stream_key=f"runner:jobs:{target_host}",
                envelope_type="runner.resume_session.v1",
                payload={
                    "type": "runner.resume_session.v1",
                    "idempotency_key": str(_uuid.uuid4()),
                    "session_id": str(session_id),
                    "guild_id": guild_id,
                    "host_id": target_host,
                    "thread_id": sess.thread_id or "",
                },
            )
    return True


async def resume_session(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    guild_id: str,
    host_id: str,
) -> bool:
    return await run_resume_saga(
        db_factory, session_id=session_id, guild_id=guild_id, host_id=host_id
    )


async def run_restart_saga(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    guild_id: str,
    requested_by_id: str,
    deployment_secret_key: str,  # unused; kept for signature compatibility
) -> str:
    import uuid as _uuid

    from sqlalchemy import update

    from discode.db.models import Event
    from discode.db.models import Session as SessionModel

    session_uuid = _uuid.UUID(session_id) if isinstance(session_id, str) else session_id
    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                update(SessionModel)
                .where(
                    SessionModel.id == session_uuid,
                    SessionModel.guild_id == guild_id,
                    SessionModel.status.in_(["running", "idle", "stopped"]),
                )
                .values(tool_resume_token=None, status="running")
            )
            if (result.rowcount or 0) == 0:
                raise ValueError(f"session {session_id} not in restartable state")
            db.add(
                Event(
                    idempotency_key=str(_uuid.uuid4()),
                    guild_id=guild_id,
                    session_id=session_uuid,
                    actor_id=requested_by_id,
                    type="session.restart.requested",
                    payload={},
                )
            )
    return session_id


async def restart_session(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    guild_id: str,
    requested_by_id: str,
    deployment_secret_key: str,
) -> str:
    return await run_restart_saga(
        db_factory,
        session_id=session_id,
        guild_id=guild_id,
        requested_by_id=requested_by_id,
        deployment_secret_key=deployment_secret_key,
    )


async def invite_user_to_session(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    guild_id: str,
    invitee_id: str,
    invitee_username: str,
) -> bool:
    """Add invitee as a SessionMember (role='operator'). Idempotent.

    Returns True if added or already a member; False if session not found
    or doesn't belong to the given guild.
    """
    import uuid as _uuid

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from discode.db.models import Event, SessionMember, User
    from discode.db.models import Session as SessionModel

    session_uuid = _uuid.UUID(session_id) if isinstance(session_id, str) else session_id

    async with db_factory() as db:
        async with db.begin():
            sess = await db.get(SessionModel, session_uuid)
            if sess is None or sess.guild_id != guild_id:
                return False

            await db.execute(
                pg_insert(User)
                .values(id=invitee_id, username=invitee_username)
                .on_conflict_do_nothing(index_elements=["id"])
            )

            await db.execute(
                pg_insert(SessionMember)
                .values(
                    session_id=session_uuid,
                    user_id=invitee_id,
                    role="operator",
                )
                .on_conflict_do_nothing(
                    constraint="session_members_pkey",
                )
            )

            db.add(
                Event(
                    idempotency_key=str(_uuid.uuid4()),
                    guild_id=guild_id,
                    session_id=session_uuid,
                    actor_id=None,
                    type="session.invite.requested",
                    payload={"invitee_id": invitee_id, "role": "operator"},
                )
            )

    return True


async def invite_member(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    guild_id: str,
    invitee_id: str,
    invitee_username: str,
) -> bool:
    return await invite_user_to_session(
        db_factory,
        session_id=session_id,
        guild_id=guild_id,
        invitee_id=invitee_id,
        invitee_username=invitee_username,
    )


async def is_session_member(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: str,
) -> bool:
    """Return True if *user_id* is the owner OR an explicit member of *session_id*.

    Checks ``sessions.owner_id`` first (no extra JOIN needed), then falls back
    to a ``session_members`` lookup.
    """
    session_uuid = uuid.UUID(session_id)

    # Fast path: check owner_id on the session row.
    session_row = await db.get(SessionModel, session_uuid)
    if session_row is None:
        return False
    if session_row.owner_id == user_id:
        return True

    # Slow path: explicit membership.
    result = await db.execute(
        sa.select(SessionMember).where(
            SessionMember.session_id == session_uuid,
            SessionMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def is_session_owner_or_admin(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    guild_id: str,
    user_id: str,
    member_roles: list[str],
    member_role_names: list[str] | None = None,
) -> bool:
    """Return True if *user_id* owns *session_id* OR is a configured guild admin.

    Owner-or-admin is the authorization model for owner-gated lifecycle commands
    (/stop, /archive, /resume, /restart). Plain session membership (invited
    operator) is NOT sufficient — that is the bug these commands previously had.
    """
    from discode.bot.policies.admin_auth import is_guild_admin_authorized

    session_uuid = uuid.UUID(session_id) if isinstance(session_id, str) else session_id
    async with db_factory() as db:
        sess_row = await db.get(SessionModel, session_uuid)
        if sess_row is not None and sess_row.owner_id == user_id:
            return True
        return await is_guild_admin_authorized(
            db,
            guild_id=guild_id,
            member_roles=member_roles,
            member_role_names=member_role_names,
        )
