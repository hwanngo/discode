from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
import uuid
from collections.abc import Awaitable, Callable, Generator
from typing import Any

import uvicorn
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.config import get_settings
from discode.metrics import REGISTRY
from discode.queues.consumers.runner_cg import (
    RUNNER_JOB_MAX_DELIVERIES,
    RunnerConsumerGroup,
)
from discode.queues.relay import RelayLoop, supervise_relay
from discode.runner.control_api import app as control_api_app
from discode.runner.resume_runner import run_resume_job
from discode.runner.saga_create import parse_create_job_payload, run_create_job
from discode.runner.saga_input import InputJobPayload, close_reply_client, run_input_job
from discode.runner.saga_resume import recover_sessions
from discode.runtime import AppRuntime

logger = logging.getLogger(__name__)


RUNNER_GROUP = "runner-cg"
# Pause before retrying a failed Redis stream read (outage, failover).
_STREAM_RETRY_SECONDS = 1.0
_CONTROL_API_STOP_SECONDS = 5.0


def build_session_lookup(
    db_factory: async_sessionmaker[AsyncSession],
) -> Callable[[str], Awaitable[dict[str, Any] | None]]:
    """Return an async lookup `(session_id) -> {thread_id, status} | None`.

    Used by the control API's reply endpoint to validate session liveness
    before posting to Discord.
    """

    async def lookup(session_id: str) -> dict[str, Any] | None:
        try:
            uuid.UUID(session_id)
        except ValueError:
            return None
        async with db_factory() as db:
            result = await db.execute(
                sa_text(
                    "SELECT thread_id, status, guild_id, cwd_realpath, lifecycle_version "
                    "FROM sessions WHERE id = :sid AND host_id = :host"
                ),
                {"sid": session_id, "host": get_settings().RUNNER_HOST_ID},
            )
            row = result.fetchone()
        if row is None:
            return None
        return dict(row._mapping)

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
            lifecycle_version=payload.get("lifecycle_version"),
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

    if envelope_type in ("runner.stop_session.v1", "runner.restart_session.v1"):
        sid = str(payload.get("session_id", ""))
        version = payload.get("lifecycle_version")
        async with db_factory() as db, db.begin():
            row = (
                await db.execute(
                    sa_text(
                        "SELECT lifecycle_version, status FROM sessions "
                        "WHERE id=:sid AND host_id=:hid"
                    ),
                    {"sid": sid, "hid": host_id},
                )
            ).first()
            if row is None or version != row[0]:
                # Superseded job (e.g. the janitor already forced 'stopped'). Still
                # drop the cached context of a session that can no longer run.
                if row is None or row[1] not in ("creating", "resuming", "running", "idle"):
                    session_store.pop(sid, None)
                return True
            if envelope_type == "runner.stop_session.v1":
                await db.execute(
                    sa_text(
                        "UPDATE sessions SET status='stopped' WHERE id=:sid "
                        "AND status='stopping' AND lifecycle_version=:version"
                    ),
                    {"sid": sid, "version": version},
                )
        # In-flight execution observes the authoritative version and cancels.
        session_store.pop(sid, None)
        if envelope_type == "runner.restart_session.v1":
            async with db_factory() as lock_db, lock_db.begin():
                locked = await lock_db.scalar(
                    sa_text("SELECT pg_try_advisory_xact_lock(hashtextextended(:sid,0))"),
                    {"sid": sid},
                )
                if not locked:
                    return False
                await run_resume_job(
                    payload, host_id=host_id, session_store=session_store, db_factory=db_factory
                )
                return True
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
    """Dead-letter (retain, then ack) a message that can't be processed.

    The payload is kept in the ``discode:deadletter:<stream>`` Redis hash so an
    operator can inspect and re-XADD it; acking stops the redelivery loop.
    """
    logger.error(
        "runner dead-lettering message id=%s attempts=%d reason=%s envelope_type=%s key=%s",
        message_id,
        attempts,
        reason,
        fields.get("envelope_type", ""),
        consumer.dead_letter_key,
    )
    await consumer.dead_letter(message_id, fields, reason=reason, attempts=attempts)
    REGISTRY.increment("discode_runner_deadletters_total")


async def _consume_runner_stream(
    consumer: RunnerConsumerGroup,
    handler: Callable[[dict[str, str]], Awaitable[bool]],
    max_deliveries: int = RUNNER_JOB_MAX_DELIVERIES,
) -> None:
    # Local attempt tracking. `delivery_count` (XPENDING) returns 0 on Redis
    # errors, which would silently disable any budget — so we fail closed using
    # our own per-message counter and take the larger of the two counts.
    local_attempts: dict[str, int] = {}
    while True:
        try:
            messages = await consumer.claim_pending()
            if not messages:
                messages = await consumer.read_new(count=10, block_ms=250)
        except Exception:
            logger.exception("runner stream read failed; retrying")
            await asyncio.sleep(_STREAM_RETRY_SECONDS)
            continue
        for message_id, fields in messages:
            local_attempts[message_id] = local_attempts.get(message_id, 0) + 1
            try:
                await _process_runner_message(
                    consumer, handler, message_id, fields, local_attempts, max_deliveries
                )
            except Exception:
                # Redis failed while acking/counting; the entry stays pending and
                # is reclaimed after RUNNER_JOB_PEL_IDLE_MS.
                logger.exception("runner stream bookkeeping failed message_id=%s", message_id)


async def _process_runner_message(
    consumer: RunnerConsumerGroup,
    handler: Callable[[dict[str, str]], Awaitable[bool]],
    message_id: str,
    fields: dict[str, str],
    local_attempts: dict[str, int],
    max_deliveries: int,
) -> None:
    try:
        handled = await handler(fields)
    except Exception as exc:
        logger.warning(
            "runner message failed message_id=%s type=%s", message_id, type(exc).__name__
        )
        reason = "handler_exception"
    else:
        if handled:
            await consumer.ack(message_id)
            local_attempts.pop(message_id, None)
            return
        # Handler asked us to leave the message pending (e.g. deferred input).
        # Honour transient retries, but a message that never makes progress must
        # not loop forever — drop after the budget.
        reason = "unhandled_budget_exhausted"
    server_count = await consumer.delivery_count(message_id)
    attempts = max(local_attempts[message_id], server_count)
    if attempts >= max_deliveries:
        await _drop_poison_message(consumer, message_id, fields, attempts, reason)
        local_attempts.pop(message_id, None)


async def _consume_input_stream(
    consumer: RunnerConsumerGroup,
    handler: Callable[[dict[str, str]], Awaitable[bool]],
    concurrency: int = 8,
    stop: asyncio.Event | None = None,
    drain_timeout_seconds: float = 0,
) -> None:
    """Bound total admitted jobs and serialize same-session jobs in stream order.

    When ``stop`` is set, no new jobs are admitted; started turns get up to
    ``drain_timeout_seconds`` to finish, and queued turns stay pending in Redis
    for redelivery after restart. Cancellation kills everything immediately.
    """
    stop = stop or asyncio.Event()
    active: set[asyncio.Task[None]] = set()
    tails: dict[str, asyncio.Task[None]] = {}
    inflight: set[str] = set()
    slots = asyncio.Semaphore(concurrency)
    queue_capacity = concurrency * 8

    async def process(
        mid: str, fields: dict[str, str], previous: asyncio.Task[None] | None
    ) -> None:
        try:
            if previous is not None:
                await previous
            async with slots:
                if stop.is_set():
                    return  # left pending; redelivered after restart
                if await handler(fields):
                    await consumer.ack(mid)
        except Exception as exc:
            logger.warning("input handler deferred id=%s type=%s", mid, type(exc).__name__)
        finally:
            inflight.discard(mid)

    try:
        while not stop.is_set():
            active = {task for task in active if not task.done()}
            tails = {sid: task for sid, task in tails.items() if not task.done()}
            if len(active) >= queue_capacity:
                await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                continue
            try:
                messages = await consumer.claim_pending()
                if not messages:
                    messages = await consumer.read_new(
                        count=queue_capacity - len(active), block_ms=250
                    )
            except Exception:
                logger.exception("input stream read failed; retrying")
                await asyncio.sleep(_STREAM_RETRY_SECONDS)
                continue
            if messages and all(mid in inflight for mid, _ in messages):
                await asyncio.sleep(0.05)
            for mid, fields in messages:
                if mid in inflight:
                    continue
                while len(active) >= queue_capacity:
                    _, active = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                try:
                    sid = str(json.loads(fields.get("payload", "{}"))["session_id"])
                except ValueError, KeyError, TypeError:
                    await _drop_poison_message(consumer, mid, fields, 1, "invalid_payload")
                    continue
                inflight.add(mid)
                task = asyncio.create_task(process(mid, fields, tails.get(sid)))
                tails[sid] = task
                active.add(task)
        active = {task for task in active if not task.done()}
        if active:
            logger.info("draining %d input job(s), up to %ss", len(active), drain_timeout_seconds)
            _, pending = await asyncio.wait(active, timeout=drain_timeout_seconds)
            if pending:
                logger.warning("drain timed out; cancelling %d input job(s)", len(pending))
    finally:
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)


class _ControlServer(uvicorn.Server):
    """uvicorn server that leaves SIGINT/SIGTERM to the runner.

    uvicorn's default ``capture_signals`` re-raises the signal with the default
    handler after serving, which kills the process before the runner's cleanup
    (tool process-group kill, marking the host offline) can run.
    """

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        yield


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
                            VALUES (:id, 'online', now())
                            ON CONFLICT (id) DO UPDATE
                              SET status = 'online',
                                  last_heartbeat_at = EXCLUDED.last_heartbeat_at
                        """),
                        # Database clock, like the janitor's staleness check: host
                        # clock skew must not mark a live runner offline.
                        {"id": host_id},
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
    try:
        await _run_runner(runtime)
    finally:
        client = getattr(control_api_app.state, "discord_client", None)
        if client is not None:
            await client.close()
        await close_reply_client()
        await runtime.close()


async def _run_runner(runtime: AppRuntime) -> None:
    logger.info(
        "runner config: RUNNER_CONTROL_BIND=%s RUNNER_CONTROL_AUTH_set=%s",
        runtime.settings.RUNNER_CONTROL_BIND,
        bool(runtime.settings.RUNNER_CONTROL_AUTH),
    )
    host_id = runtime.settings.RUNNER_HOST_ID
    from discode.security.execution_policy import ensure_execution_allowed

    ensure_execution_allowed(runtime.settings)
    runtime.start_metrics(runtime.settings.RUNNER_METRICS_BIND)
    consumer_id = f"{socket.gethostname()}-{os.getpid()}"
    session_store: dict[str, dict[str, Any]] = {}

    control_host, control_port = _parse_bind(runtime.settings.RUNNER_CONTROL_BIND)
    control_server = _ControlServer(
        uvicorn.Config(app=control_api_app, host=control_host, port=control_port, log_level="info")
    )

    # Attach reply-pipeline collaborators to the control API's state so the
    # POST /v1/sessions/{sid}/reply endpoint can route Discord messages.
    from discode.dispatcher.discord_client import DiscordRestClient
    from discode.dispatcher.rate_limiter import ChannelRateLimiter

    discord_client = DiscordRestClient(token=runtime.settings.DISCORD_TOKEN)
    control_api_app.state.discord_client = discord_client
    await discord_client.start()
    rate_limiter = ChannelRateLimiter()
    discord_client.set_retry_after_callback(rate_limiter.note_retry_after)

    control_api_app.state.discord_client = discord_client
    control_api_app.state.rate_limiter = rate_limiter
    control_api_app.state.session_lookup = build_session_lookup(runtime.db_factory)
    control_api_app.state.db_factory = runtime.db_factory

    async def output_quota(sid: str, nonce: str, size: int) -> bool:
        # Atomic, shared across processes; replayed chunks consume the budget once.
        allowed = await runtime.redis.eval(
            """
            if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 1 then return 1 end
            local total = tonumber(redis.call('HGET', KEYS[1], 'total') or '0')
            if total + tonumber(ARGV[2]) > tonumber(ARGV[3]) then return 0 end
            redis.call('HSET', KEYS[1], ARGV[1], 1)
            redis.call('HINCRBY', KEYS[1], 'total', ARGV[2])
            if redis.call('TTL', KEYS[1]) < 0 then redis.call('EXPIRE', KEYS[1], 60) end
            return 1
            """,
            1,
            f"output:quota:{sid}",
            nonce,
            size,
            runtime.settings.MAX_OUTPUT_BYTES_PER_SESSION_PER_MIN,
        )
        return bool(allowed)

    control_api_app.state.output_quota = output_quota

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

    heartbeat_interval = runtime.settings.RUNNER_HEARTBEAT_INTERVAL_SECONDS
    stop_input = asyncio.Event()
    input_task = asyncio.create_task(
        _consume_input_stream(
            input_consumer,
            _handler,
            stop=stop_input,
            drain_timeout_seconds=runtime.settings.RUNNER_DRAIN_TIMEOUT_SECONDS,
        ),
        name="input-jobs",
    )
    tasks = [
        control_task := asyncio.create_task(
            _serve_uvicorn(control_server), name="runner-control-api"
        ),
        asyncio.create_task(supervise_relay(relay), name="runner-relay"),
        asyncio.create_task(
            _consume_runner_stream(
                runner_consumer, _handler, runtime.settings.RUNNER_JOB_MAX_DELIVERIES
            ),
            name="runner-jobs",
        ),
        input_task,
        asyncio.create_task(
            _heartbeat_loop(host_id, runtime.db_factory, heartbeat_interval),
            name="runner-heartbeat",
        ),
    ]

    def _request_stop(sig: signal.Signals) -> None:
        # First signal drains in-flight turns; a second one skips the drain.
        if not stop_input.is_set():
            logger.info("received %s; draining", sig.name)
            stop_input.set()
        else:
            logger.warning("received %s again; cancelling in-flight turns", sig.name)
            input_task.cancel()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop, sig)
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if done == {input_task} and stop_input.is_set():
            logger.info("runner drained; shutting down")
            return
        for task in done:
            task.result()
        raise RuntimeError("runner service stopped unexpectedly")
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.remove_signal_handler(sig)
        control_server.should_exit = True
        # Let uvicorn finish its lifespan shutdown; cancelling it mid-way logs a
        # spurious CancelledError traceback.
        await asyncio.wait({control_task}, timeout=_CONTROL_API_STOP_SECONDS)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _mark_runner_offline(host_id, runtime.db_factory)
        try:
            await discord_client.close()
        except Exception:
            logger.exception("failed to close DiscordRestClient")


def main() -> None:

    from discode.log import configure_logging

    configure_logging()
    try:
        asyncio.run(run_runner())
    except KeyboardInterrupt:
        logger.info("runner stopped")
