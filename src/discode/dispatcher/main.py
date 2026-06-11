from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.dispatcher.consumer import DISPATCHER_MAX_DELIVERIES, DispatcherConsumer
from discode.dispatcher.discord_client import DiscordRestClient
from discode.dispatcher.handlers.archive_thread import handle_archive_thread
from discode.dispatcher.handlers.terminal_notice import _claim_act_finalize, handle_terminal_notice
from discode.dispatcher.rate_limiter import ChannelRateLimiter
from discode.runtime import AppRuntime
from discode.security.redaction import apply_common_redaction

logger = logging.getLogger(__name__)


async def _handle_system_notice(
    envelope: dict[str, Any],
    *,
    send_to_discord: Callable[..., Awaitable[str]],
    db_factory: async_sessionmaker[AsyncSession],
) -> bool:
    thread_id = str(envelope.get("thread_id", ""))
    message = str(envelope.get("message", ""))
    idempotency_key = str(envelope.get("idempotency_key", ""))
    session_id = str(envelope.get("session_id", ""))
    if not thread_id or not message:
        return True
    # DB idempotency gate (claim-then-finalize, mirrors terminal_notice) so
    # at-least-once relay does not produce duplicate system notices. If the
    # Discord send raises, the claim is left unfinalized so redelivery retries.
    if idempotency_key:
        await _claim_act_finalize(
            db_factory,
            etype="discord.system_notice.v1",
            ikey=idempotency_key,
            session_id=session_id,
            side_effect=lambda: send_to_discord(thread_id, message, idempotency_key),
        )
    else:
        await send_to_discord(thread_id, message, None)
    return True


async def dispatch_discord_envelope(
    fields: dict[str, str],
    *,
    db_factory: async_sessionmaker[AsyncSession],
    send_to_discord: Callable[..., Awaitable[str]],
    archive_discord_thread: Callable[..., Awaitable[None]],
    rate_limiter: ChannelRateLimiter | None = None,
) -> bool:
    payload_raw = fields.get("payload")
    if payload_raw is None:
        logger.warning("dispatcher message missing payload field")
        return True

    payload = json.loads(payload_raw)
    if not isinstance(payload, dict):
        logger.warning("dispatcher payload is not an object")
        return True

    envelope_type = fields.get("envelope_type") or str(payload.get("type", ""))

    if envelope_type == "discord.output_chunk.v1":
        # Legacy envelope from the streaming pipeline — ack and drop.
        logger.info(
            "dropping legacy output_chunk envelope sid=%s",
            payload.get("session_id", ""),
        )
        return True

    if envelope_type == "discord.system_notice.v1":
        return await _handle_system_notice(
            payload, send_to_discord=send_to_discord, db_factory=db_factory
        )

    if envelope_type == "discord.terminal_notice.v1":
        return await handle_terminal_notice(
            payload,
            db_factory=db_factory,
            send_to_discord=send_to_discord,
            archive_discord_thread=archive_discord_thread,
        )

    if envelope_type == "discord.archive_thread.v1":
        return await handle_archive_thread(
            payload,
            db_factory=db_factory,
            archive_discord_thread=archive_discord_thread,
        )

    logger.warning("unknown dispatcher envelope type: %s", envelope_type)
    return True


async def _consume_dispatcher_stream(
    consumer: DispatcherConsumer,
    handler: Callable[[dict[str, str]], Awaitable[bool]],
) -> None:
    while True:
        messages = await consumer.claim_pending()
        if not messages:
            messages = await consumer.read_next()
        for message_id, fields in messages:
            try:
                handled = await handler(fields)
            except Exception:
                logger.exception("dispatcher message failed message_id=%s", message_id)
                # Check redelivery budget; ack to drop if exhausted.
                count = await consumer.delivery_count(message_id)
                if count >= DISPATCHER_MAX_DELIVERIES:
                    logger.error(
                        "dispatcher dropping message after %d failures id=%s",
                        count,
                        message_id,
                    )
                    await consumer.ack(message_id)
                continue
            if handled:
                await consumer.ack(message_id)
            else:
                count = await consumer.delivery_count(message_id)
                if count >= DISPATCHER_MAX_DELIVERIES:
                    logger.error(
                        "dispatcher dropping unhandled message after %d redeliveries id=%s",
                        count,
                        message_id,
                    )
                    await consumer.ack(message_id)


async def run_dispatcher() -> None:
    logger.info("dispatcher starting")
    runtime = AppRuntime.from_env()
    consumer_id = f"{socket.gethostname()}-{os.getpid()}"
    consumer = DispatcherConsumer(runtime.redis, consumer_id)
    await consumer.ensure_group()

    discord_client = DiscordRestClient(token=runtime.settings.DISCORD_TOKEN)
    rate_limiter = ChannelRateLimiter(capacity=5, refill_per_second=1.0)
    discord_client.set_retry_after_callback(rate_limiter.note_retry_after)
    try:
        await discord_client.start()

        async def _send_to_discord(
            thread_id: str,
            message: str,
            nonce: str | None = None,
        ) -> str:
            # Single outbound chokepoint: ALL user-visible text (system notices
            # AND relayed replies routed through the dispatcher) is redacted here
            # before it reaches Discord.
            redacted = apply_common_redaction(message)
            return await discord_client.send_message(
                thread_id=thread_id, content=redacted, nonce=nonce
            )

        async def _archive_discord_thread(thread_id: str) -> None:
            await discord_client.archive_thread(thread_id=thread_id)

        async def _handler(fields: dict[str, str]) -> bool:
            return await dispatch_discord_envelope(
                fields,
                db_factory=runtime.db_factory,
                send_to_discord=_send_to_discord,
                archive_discord_thread=_archive_discord_thread,
                rate_limiter=rate_limiter,
            )

        task = asyncio.create_task(
            _consume_dispatcher_stream(consumer, _handler),
            name="dispatcher-outbound",
        )
        try:
            await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await discord_client.close()
    finally:
        await runtime.close()


def main() -> None:
    try:
        asyncio.run(run_dispatcher())
    except KeyboardInterrupt:
        logger.info("dispatcher stopped")
