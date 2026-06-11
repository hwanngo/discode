from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import uvicorn
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.queues.consumers.runner_cg import (
    RUNNER_JOB_MAX_DELIVERIES,
    RunnerConsumerGroup,
)
from discode.queues.relay import RelayLoop
from discode.runner.control_api import app as control_api_app
from discode.runner.resume_runner import run_resume_job
from discode.runner.saga_create import parse_create_job_payload, run_create_job
from discode.runner.saga_input import InputJobPayload, run_input_job
from discode.runner.saga_resume import recover_sessions
from discode.runtime import AppRuntime

logger = logging.getLogger(__name__)


RUNNER_GROUP = "runner-cg"


def build_session_lookup(
    db_factory: async_sessionmaker[AsyncSession],
) -> Callable[[str], Awaitable[dict[str, Any] | None]]:
    """Return an async lookup `(session_id) -> {thread_id, status} | None`.

    Used by the control API's reply endpoint to validate session liveness
    before posting to Discord.
    """

    async def lookup(session_id: str) -> dict[str, Any] | None:
        async with db_factory() as db:
            result = await db.execute(
                sa_text("SELECT thread_id, status FROM sessions WHERE id = :sid"),
                {"sid": session_id},
            )
            row = result.fetchone()
        if row is None:
            return None
        return {"thread_id": row.thread_id, "status": row.status}

    return lookup


def _parse_bind(bind: str) -> tuple[str, int]:
    host, port = bind.rsplit(":", 1)
    return host, int(port)


async def dispatch_runner_job(
    fields: dict[str, str],
    *,
    host_id: str,
    db_factory: async_sessionmaker[AsyncSession],
    session_store: dict[str, dict[str, Any]],
) -> bool:
    payload_raw = fields.get("payload")
    if payload_raw is None:
        logger.warning("runner message missing payload field")
        return True

    payload = json.loads(payload_raw)
    if not isinstance(payload, dict):
        logger.warning("runner payload is not an object")
        return True

    envelope_type = fields.get("envelope_type") or str(payload.get("type", ""))

    if envelope_type == "runner.create_session.v1":
        create_payload = parse_create_job_payload(payload)
        await run_create_job(
            create_payload,
            host_id=host_id,
            db_factory=db_factory,
            session_store=session_store,
        )
        return True

    if envelope_type == "runner.send_input.v1":
        input_payload = InputJobPayload(
            session_id=str(payload["session_id"]),
            guild_id=str(payload["guild_id"]),
            thread_id=str(payload.get("thread_id", "")),
            host_id=str(payload.get("host_id", host_id)),
            text=str(payload["text"]),
            idempotency_key=str(payload["idempotency_key"]),
        )
        # run_input_job returns one of: "delivered", "skipped_replay",
        # "deferred", "aborted". Only "deferred" is transient (a live lease is
        # held by another handler, or the session isn't in the store yet — e.g.
        # input racing /start, or a resumed session). For "deferred" we must
        # leave the message PENDING so XAUTOCLAIM redelivers it; acking would
        # silently lose the user's input. The other outcomes are terminal
        # decisions (delivered / replay-skipped / Stage 2.5 abort) and should
        # be acked so we don't redeliver them forever.
        outcome = await run_input_job(
            input_payload,
            session_store=session_store,
            db_factory=db_factory,
        )
        if outcome == "deferred":
            logger.info(
                "input deferred sid=%s — leaving message pending for retry",
                input_payload.session_id,
            )
            return False
        return True

    if envelope_type == "runner.stop_session.v1":
        # Stop saga: just remove from session_store and let the DB update be handled
        # by the bot-side stop saga (status 'stopping'). No tmux to kill.
        session_store.pop(str(payload.get("session_id", "")), None)
        return True

    if envelope_type == "runner.resume_session.v1":
        await run_resume_job(
            payload,
            host_id=host_id,
            session_store=session_store,
            db_factory=db_factory,
        )
        return True

    logger.warning("unknown runner envelope type: %s", envelope_type)
    return True


async def _drop_poison_message(
    consumer: RunnerConsumerGroup,
    message_id: str,
    fields: dict[str, str],
    attempts: int,
    reason: str,
) -> None:
    """Ack (remove from PEL) and dead-letter a message that can't be processed.

    There is currently no dedicated dead-letter stream for the runner; until one
    exists we ack to stop the infinite redelivery loop and emit a structured
    error log carrying enough context to recover the payload manually.
    """
    logger.error(
        "runner dropping poison message id=%s attempts=%d reason=%s "
        "envelope_type=%s payload=%s",
        message_id,
        attempts,
        reason,
        fields.get("envelope_type", ""),
        fields.get("payload", "")[:2000],
    )
    await consumer.ack(message_id)


async def _consume_runner_stream(
    consumer: RunnerConsumerGroup,
    handler: Callable[[dict[str, str]], Awaitable[bool]],
) -> None:
    # Local attempt tracking. `delivery_count` (XPENDING) returns 0 on Redis
    # errors, which would silently disable any budget — so we fail closed using
    # our own per-message counter and take the larger of the two counts.
    local_attempts: dict[str, int] = {}
    while True:
        messages = await consumer.claim_pending()
        if not messages:
            messages = await consumer.read_new(count=10, block_ms=250)
        for message_id, fields in messages:
            local_attempts[message_id] = local_attempts.get(message_id, 0) + 1
            try:
                handled = await handler(fields)
            except Exception:
                logger.exception("runner message failed message_id=%s", message_id)
                server_count = await consumer.delivery_count(message_id)
                attempts = max(local_attempts[message_id], server_count)
                if attempts >= RUNNER_JOB_MAX_DELIVERIES:
                    await _drop_poison_message(
                        consumer, message_id, fields, attempts, "handler_exception"
                    )
                    local_attempts.pop(message_id, None)
                continue
            if handled:
                await consumer.ack(message_id)
                local_attempts.pop(message_id, None)
            else:
                # Handler asked us to leave the message pending (e.g. deferred
                # input). Honour transient retries, but a message that never
                # makes progress must not loop forever — drop after the budget.
                server_count = await consumer.delivery_count(message_id)
                attempts = max(local_attempts[message_id], server_count)
                if attempts >= RUNNER_JOB_MAX_DELIVERIES:
                    await _drop_poison_message(
                        consumer, message_id, fields, attempts, "unhandled_budget_exhausted"
                    )
                    local_attempts.pop(message_id, None)


async def _serve_uvicorn(server: uvicorn.Server) -> None:
    await server.serve()


async def _heartbeat_loop(
    host_id: str,
    db_factory: async_sessionmaker[AsyncSession],
    interval_seconds: int = 30,
) -> None:
    """Register this runner as online and send periodic heartbeats."""
    while True:
        try:
            async with db_factory() as db:
                async with db.begin():
                    await db.execute(
                        sa_text("""
                            INSERT INTO runner_hosts (id, status, last_heartbeat_at)
                            VALUES (:id, 'online', :now)
                            ON CONFLICT (id) DO UPDATE
                              SET status = 'online',
                                  last_heartbeat_at = EXCLUDED.last_heartbeat_at
                        """),
                        {"id": host_id, "now": datetime.now(UTC)},
                    )
        except Exception:
            logger.exception("heartbeat failed for host %s", host_id)
        await asyncio.sleep(interval_seconds)


async def _mark_runner_offline(
    host_id: str,
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    try:
        async with db_factory() as db:
            async with db.begin():
                await db.execute(
                    sa_text("UPDATE runner_hosts SET status = 'offline' WHERE id = :id"),
                    {"id": host_id},
                )
    except Exception:
        logger.exception("failed to mark runner %s offline", host_id)


async def run_runner() -> None:
    logger.info("runner starting")
    runtime = AppRuntime.from_env()
    logger.info(
        "runner config: RUNNER_CONTROL_BIND=%s RUNNER_CONTROL_AUTH_set=%s",
        runtime.settings.RUNNER_CONTROL_BIND,
        bool(runtime.settings.RUNNER_CONTROL_AUTH),
    )
    host_id = os.environ.get("DISCODE_RUNNER_HOST_ID", "local-dev")
    consumer_id = f"{socket.gethostname()}-{os.getpid()}"
    session_store: dict[str, dict[str, Any]] = {}

    control_host, control_port = _parse_bind(runtime.settings.RUNNER_CONTROL_BIND)
    control_server = uvicorn.Server(
        uvicorn.Config(app=control_api_app, host=control_host, port=control_port, log_level="info")
    )

    # Attach reply-pipeline collaborators to the control API's state so the
    # POST /v1/sessions/{sid}/reply endpoint can route Discord messages.
    from discode.dispatcher.discord_client import DiscordRestClient
    from discode.dispatcher.rate_limiter import ChannelRateLimiter

    discord_client = DiscordRestClient(token=runtime.settings.DISCORD_TOKEN)
    await discord_client.start()
    rate_limiter = ChannelRateLimiter()
    discord_client.set_retry_after_callback(rate_limiter.note_retry_after)

    control_api_app.state.discord_client = discord_client
    control_api_app.state.rate_limiter = rate_limiter
    control_api_app.state.session_lookup = build_session_lookup(runtime.db_factory)

    runner_consumer = RunnerConsumerGroup(
        runtime.redis,
        f"runner:jobs:{host_id}",
        RUNNER_GROUP,
        consumer_id,
    )
    input_consumer = RunnerConsumerGroup(
        runtime.redis,
        f"input:jobs:{host_id}",
        RUNNER_GROUP,
        consumer_id,
    )
    await runner_consumer.ensure_group()
    await input_consumer.ensure_group()

    recovered, failed = await recover_sessions(
        runtime.db_factory,
        host_id=host_id,
        session_store=session_store,
    )
    logger.info("session recovery: recovered=%d failed=%d", recovered, failed)

    relay = RelayLoop(producer="runner", redis_client=runtime.redis, db_factory=runtime.db_factory)

    async def _handler(fields: dict[str, str]) -> bool:
        return await dispatch_runner_job(
            fields,
            host_id=host_id,
            db_factory=runtime.db_factory,
            session_store=session_store,
        )

    heartbeat_interval = int(os.environ.get("RUNNER_HEARTBEAT_INTERVAL_SECONDS", "30"))
    tasks = [
        asyncio.create_task(_serve_uvicorn(control_server), name="runner-control-api"),
        asyncio.create_task(relay.run(), name="runner-relay"),
        asyncio.create_task(_consume_runner_stream(runner_consumer, _handler), name="runner-jobs"),
        asyncio.create_task(_consume_runner_stream(input_consumer, _handler), name="input-jobs"),
        asyncio.create_task(
            _heartbeat_loop(host_id, runtime.db_factory, heartbeat_interval),
            name="runner-heartbeat",
        ),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        control_server.should_exit = True
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _mark_runner_offline(host_id, runtime.db_factory)
        try:
            await discord_client.close()
        except Exception:
            logger.exception("failed to close DiscordRestClient")
        await runtime.close()


def main() -> None:
    try:
        asyncio.run(run_runner())
    except KeyboardInterrupt:
        logger.info("runner stopped")
