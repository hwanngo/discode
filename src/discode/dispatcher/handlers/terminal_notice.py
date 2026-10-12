from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


async def handle_terminal_notice(
    envelope: dict[str, Any],
    *,
    db_factory: async_sessionmaker[AsyncSession],
    send_to_discord: Callable[..., Awaitable[str]],
    archive_discord_thread: Callable[..., Awaitable[None]],
) -> bool:
    """
    Handle discord.terminal_notice.v1 envelope (2-stage: post then archive).

    Both stages use claim-then-finalize (mirrors runner.saga_input) so a Discord
    failure does not poison the idempotency key:

    Stage 1 (#post): claim with outcome=NULL, post the message, then finalize
    (outcome='done'). If the post raises, the claim stays unfinalized and
    redelivery retries.

    Stage 2 (#archive, if then_archive=True): claim with outcome=NULL, archive
    the thread, then finalize. Same crash-safety.

    A claim whose outcome IS NOT NULL is a genuine duplicate -> skip that stage.
    """
    session_id = envelope.get("session_id", "")
    thread_id = envelope.get("thread_id", "")
    ikey = envelope.get("idempotency_key", "")
    message = envelope.get("message", "")
    then_archive_raw = envelope.get("then_archive", False)
    then_archive = then_archive_raw is True or then_archive_raw == "true"

    # Stage 1: post
    post_ikey = f"{ikey}#post"
    if message:
        await _claim_act_finalize(
            db_factory,
            etype="discord.terminal_notice.v1#post",
            ikey=post_ikey,
            session_id=session_id,
            side_effect=lambda: send_to_discord(thread_id, message, post_ikey),
        )

    # Stage 2: archive if requested
    if then_archive:

        async def record_archive(db: AsyncSession) -> None:
            await db.execute(
                text(
                    "UPDATE sessions SET discord_thread_archived_at=now(), "
                    "thread_archive_requested_at=now() WHERE id=:sid"
                ),
                {"sid": session_id or None},
            )

        archive_ikey = f"{ikey}#archive"
        await _claim_act_finalize(
            db_factory,
            etype="discord.terminal_notice.v1#archive",
            ikey=archive_ikey,
            session_id=session_id,
            side_effect=lambda: archive_discord_thread(thread_id),
            finalize=record_archive,
        )

    return True


async def _claim_act_finalize(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    etype: str,
    ikey: str,
    session_id: str,
    side_effect: Callable[[], Awaitable[Any]],
    finalize: Callable[[AsyncSession], Awaitable[None]] | None = None,
) -> None:
    """Serialize a delivery and its finalization under the database row lock.

    The transaction remains open during the bounded network operation. A crash
    rolls it back; Discord's stable nonce protects retries within its deduplication
    window. This is at-least-once delivery, not an exactly-once network guarantee.
    """
    if not ikey:
        raise ValueError("delivery requires an idempotency key")
    async with db_factory() as db, db.begin():
        await db.execute(
            text("""
                INSERT INTO idempotency_keys
                    (envelope_type, idempotency_key, session_id, expires_at)
                VALUES (:etype, :ikey, :sid, now() + interval '7 days')
                ON CONFLICT DO NOTHING
            """),
            {"etype": etype, "ikey": ikey, "sid": session_id or None},
        )
        result = await db.execute(
            text("""
                SELECT outcome FROM idempotency_keys
                WHERE envelope_type = :etype AND idempotency_key = :ikey
                FOR UPDATE
            """),
            {"etype": etype, "ikey": ikey},
        )
        if result.scalar_one() is not None:
            return
        await side_effect()
        if finalize is not None:
            await finalize(db)
        await db.execute(
            text("""
                UPDATE idempotency_keys SET outcome = 'done'
                WHERE envelope_type = :etype AND idempotency_key = :ikey
            """),
            {"etype": etype, "ikey": ikey},
        )
