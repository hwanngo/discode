from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.config import get_settings
from discode.dispatcher.consumer import DispatcherConsumer
from discode.dispatcher.discord_client import DiscordRestClient
from discode.dispatcher.handlers.archive_thread import handle_archive_thread
from discode.dispatcher.handlers.terminal_notice import _claim_act_finalize, handle_terminal_notice
from discode.dispatcher.rate_limiter import ChannelRateLimiter
from discode.metrics import REGISTRY
from discode.runtime import AppRuntime
from discode.security.redaction import apply_common_redaction

logger = logging.getLogger(__name__)


_CREATE_FAILURE_TEXT = {
    "creation_timeout": "the runner did not respond in time",
    "cwd_invalid": "the workspace is not an allowed root on the runner",
    "tool_unavailable": "the tool is unknown or disabled",
    "host_unavailable": "the runner host is unavailable",
}


def _render_notice(envelope: dict[str, Any]) -> str:
    """Turn a system notice's machine fields into user-facing text."""
    message = str(envelope.get("message", ""))
    notice_type = envelope.get("notice_type")
    if notice_type == "session_create_failed":
        reason = _CREATE_FAILURE_TEXT.get(message, message or "unknown error")
        return f"❌ Session could not start: {reason}."
    if notice_type == "session_running" and not message:
        return "✅ Session ready. Send your messages in this thread."
    return message


async def _handle_system_notice(
    envelope: dict[str, Any],
    *,
    send_to_discord: Callable[..., Awaitable[str]],
    db_factory: async_sessionmaker[AsyncSession],
) -> bool:
    thread_id = str(envelope.get("thread_id", ""))
    message = _render_notice(envelope)
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
        raise ValueError("dispatcher message missing payload")

    payload = json.loads(payload_raw)
    if not isinstance(payload, dict):
        logger.warning("dispatcher payload is not an object")
        raise ValueError("dispatcher payload is not an object")

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
    return False


async def _consume_dispatcher_stream(
    consumer: DispatcherConsumer,
    handler: Callable[[dict[str, str]], Awaitable[bool]],
) -> None:
    while True:
        try:
            messages = await consumer.claim_pending()
            if not messages:
                messages = await consumer.read_next()
        except Exception:
            logger.exception("dispatcher stream read failed; retrying")
            REGISTRY.increment("discode_dispatcher_read_errors_total")
            await asyncio.sleep(1)
            continue
        for message_id, fields in messages:
            try:
                await _process_dispatcher_message(consumer, handler, message_id, fields)
            except Exception:
                # Redis failed while acking/dead-lettering; the entry stays pending
                # and is reclaimed later. Never let this kill the consumer loop.
                logger.exception("dispatcher bookkeeping failed message_id=%s", message_id)
                REGISTRY.increment("discode_dispatcher_read_errors_total")


async def _process_dispatcher_message(
    consumer: DispatcherConsumer,
    handler: Callable[[dict[str, str]], Awaitable[bool]],
    message_id: str,
    fields: dict[str, str],
) -> None:
    try:
        handled = await handler(fields)
    except Exception:
        logger.exception("dispatcher message failed message_id=%s", message_id)
        detail = "failures"
    else:
        if handled:
            await consumer.ack(message_id)
            REGISTRY.increment("discode_dispatcher_delivered_total")
            return
        detail = "unhandled redeliveries"
    # Retain exhausted work durably before acknowledging it.
    count = await consumer.delivery_count(message_id)
    if count >= get_settings().DISPATCHER_MAX_DELIVERIES:
        logger.error(
            "dispatcher retaining dead letter after %d %s id=%s", count, detail, message_id
        )
        await consumer.dead_letter(message_id, fields)
        REGISTRY.increment("discode_dispatcher_deadletters_total")


async def run_dispatcher() -> None:
    logger.info("dispatcher starting")
    runtime = AppRuntime.from_env()
    consumer_id = f"{socket.gethostname()}-{os.getpid()}"
    consumer = DispatcherConsumer(runtime.redis, consumer_id)

    discord_client = DiscordRestClient(token=runtime.settings.DISCORD_TOKEN)
    rate_limiter = ChannelRateLimiter(capacity=5, refill_per_second=1.0)
    discord_client.set_retry_after_callback(rate_limiter.note_retry_after)
    try:
        runtime.start_metrics(runtime.settings.DISPATCHER_METRICS_BIND)
        await consumer.ensure_group()
        await discord_client.start()

        async def _send_to_discord(
            thread_id: str,
            message: str,
            nonce: str | None = None,
        ) -> str:
            # Single outbound chokepoint: ALL user-visible text (system notices
            # AND relayed replies routed through the dispatcher) is redacted here
            # before it reaches Discord.
            await rate_limiter.acquire(thread_id)
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
    finally:
        await discord_client.close()
        await runtime.close()


def main() -> None:
    from discode.log import configure_logging

    configure_logging()
    try:
        asyncio.run(run_dispatcher())
    except KeyboardInterrupt:
        logger.info("dispatcher stopped")
