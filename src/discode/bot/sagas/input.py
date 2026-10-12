from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.queues.outbox import write_outbox

logger = logging.getLogger(__name__)


async def run_input_saga(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    guild_id: str,
    thread_id: str,
    host_id: str,
    owner_id: str,
    text_content: str,
    enable_message_content: bool = False,
    actor_id: str | None = None,
) -> str:
    """
    Bot-side input: insert input transcript chunk + outbox row atomically.
    Returns the idempotency_key used.

    Validates: text_content not empty, within MAX_INPUT_BYTES_PER_MESSAGE.
    """
    from discode.security.quotas import MAX_INPUT_BYTES_PER_MESSAGE

    if not text_content.strip():
        raise ValueError("empty_input")
    if len(text_content.encode("utf-8")) > MAX_INPUT_BYTES_PER_MESSAGE():
        raise ValueError("input_too_large")

    idempotency_key = str(uuid.uuid4())

    async with db_factory() as db:
        async with db.begin():
            from datetime import UTC, datetime, timedelta

            from sqlalchemy import func, select

            from discode.config import settings
            from discode.db.models import Event, InputTurn
            from discode.db.models import Session as SessionModel
            from discode.security.quotas import (
                MAX_INPUT_BYTES_PER_SESSION_PER_MIN,
                MAX_INPUT_MESSAGES_PER_SESSION_PER_MIN,
            )

            if settings.EMERGENCY_STOP:
                raise ValueError("emergency_stop")
            row = await db.get(SessionModel, uuid.UUID(session_id), with_for_update=True)
            if row is None or row.guild_id != guild_id:
                raise ValueError("session_not_found")
            if row.status not in {"running", "idle"}:
                raise ValueError(f"session_not_ready:{row.status}")
            if not row.thread_id or not row.host_id:
                raise ValueError("session_not_ready")
            recent = (
                (
                    await db.execute(
                        select(Event.payload).where(
                            Event.session_id == row.id,
                            Event.type == "session.input.accepted",
                            Event.created_at >= datetime.now(UTC) - timedelta(minutes=1),
                        )
                    )
                )
                .scalars()
                .all()
            )
            input_bytes = len(text_content.encode("utf-8"))
            if len(recent) >= MAX_INPUT_MESSAGES_PER_SESSION_PER_MIN() or (
                sum(int(str(item.get("bytes", 0))) for item in recent) + input_bytes
                > MAX_INPUT_BYTES_PER_SESSION_PER_MIN()
            ):
                raise ValueError("input_rate_limit: retry in one minute")
            row.last_activity_at = func.now()
            row.status = "running"
            db.add(
                Event(
                    idempotency_key=idempotency_key,
                    guild_id=guild_id,
                    session_id=row.id,
                    # The member who sent it (invitee, admin /send), not the owner.
                    actor_id=actor_id or owner_id,
                    type="session.input.accepted",
                    payload={"bytes": input_bytes},
                )
            )
            db.add(
                InputTurn(
                    idempotency_key=idempotency_key,
                    session_id=row.id,
                    lifecycle_version=row.lifecycle_version,
                    status="queued",
                )
            )
            thread_id, host_id = row.thread_id, row.host_id
            # Enqueue runner.send_input.v1 via outbox
            await write_outbox(
                db,
                producer="bot",
                stream_key=f"input:jobs:{host_id}",
                envelope_type="runner.send_input.v1",
                payload={
                    "type": "runner.send_input.v1",
                    "idempotency_key": idempotency_key,
                    "session_id": session_id,
                    "guild_id": guild_id,
                    "thread_id": thread_id,
                    "host_id": host_id,
                    "text": text_content,
                    "lifecycle_version": row.lifecycle_version,
                },
            )

    return idempotency_key
