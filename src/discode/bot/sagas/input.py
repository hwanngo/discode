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
                },
            )

    return idempotency_key
