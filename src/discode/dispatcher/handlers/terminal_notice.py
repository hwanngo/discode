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
    then_archive = bool(then_archive_raw) or then_archive_raw == "true"

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
        archive_ikey = f"{ikey}#archive"
        await _claim_act_finalize(
            db_factory,
            etype="discord.terminal_notice.v1#archive",
            ikey=archive_ikey,
            session_id=session_id,
            side_effect=lambda: archive_discord_thread(thread_id),
        )

    return True


async def _claim_act_finalize(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    etype: str,
    ikey: str,
    session_id: str,
    side_effect: Callable[[], Awaitable[Any]],
) -> None:
    """Claim an idempotency key (outcome=NULL), run the side effect, then
    finalize (outcome='done'). Skips if an existing claim is already finalized.

    If ``side_effect`` raises, the claim is left unfinalized so redelivery
    retries instead of skipping.
    """
    # Claim with outcome=NULL.
    async with db_factory() as db:
        async with db.begin():
            insert_result = await db.execute(
                text("""
                    INSERT INTO idempotency_keys
                        (envelope_type, idempotency_key, session_id, expires_at)
                    VALUES
                        (:etype, :ikey, :sid, now() + interval '7 days')
                    ON CONFLICT DO NOTHING
                    RETURNING idempotency_key
                """),
                {"etype": etype, "ikey": ikey, "sid": session_id},
            )
            freshly_claimed = insert_result.fetchone() is not None

    # Existing claim already finalized -> genuine duplicate, skip.
    if not freshly_claimed:
        async with db_factory() as db:
            outcome_result = await db.execute(
                text("""
                    SELECT outcome FROM idempotency_keys
                    WHERE envelope_type = :etype AND idempotency_key = :ikey
                """),
                {"etype": etype, "ikey": ikey},
            )
            row = outcome_result.fetchone()
        if row is not None and row[0] is not None:
            return

    # Perform side effect. If it raises, we never finalize -> retry on redelivery.
    await side_effect()

    # Finalize only after success.
    async with db_factory() as db:
        async with db.begin():
            await db.execute(
                text("""
                    UPDATE idempotency_keys
                    SET outcome = 'done'
                    WHERE envelope_type = :etype AND idempotency_key = :ikey
                """),
                {"etype": etype, "ikey": ikey},
            )
