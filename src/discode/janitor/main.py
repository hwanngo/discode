from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.config import get_settings
from discode.db.advisory_lock import acquire_advisory_lock, release_advisory_lock
from discode.janitor.deadletter import scan_input_dead_letters
from discode.janitor.lifecycle import (
    sweep_runner_heartbeat_orphans,
    sweep_session_thread_binding_drift,
)
from discode.janitor.retention import (
    sweep_events,
    sweep_idempotency_keys,
    sweep_outbox,
    sweep_turn_results,
)
from discode.janitor.timeouts import (
    sweep_archive_timeout,
    sweep_creating_watchdog,
    sweep_idle_timeout,
    sweep_resuming_watchdog,
    sweep_stop_timeout,
    sweep_stopping_watchdog,
)
from discode.metrics import REGISTRY
from discode.queues.relay import RelayLoop, supervise_relay
from discode.queues.retention import trim_acknowledged
from discode.runtime import AppRuntime

logger = logging.getLogger(__name__)


# Stable advisory-lock id for the janitor sweep pass, so only one janitor
# instance runs a sweep at a time (multi-tx sweeps double-increment otherwise).
JANITOR_SWEEP_LOCK_ID = 0x4A4E_5357  # "JNSW"


async def run_sweep_once(
    *,
    db_factory: async_sessionmaker[AsyncSession],
    redis_client: object,
) -> int:
    """Run one sweep pass guarded by a Postgres advisory lock.

    Only one janitor instance holds the lock at a time; a second concurrent
    janitor fails to acquire it and skips this pass (returns -1) rather than
    double-running the multi-transaction sweeps.
    """
    async with db_factory() as lock_session:
        acquired = await acquire_advisory_lock(lock_session, JANITOR_SWEEP_LOCK_ID)
        if not acquired:
            logger.info("janitor sweep skipped: another instance holds the lock")
            return -1
        try:
            return await _run_sweep_body(db_factory=db_factory, redis_client=redis_client)
        finally:
            try:
                await release_advisory_lock(lock_session, JANITOR_SWEEP_LOCK_ID)
            except BaseException:
                # Session-level advisory locks survive rollback; a pooled connection
                # still holding it would block every later sweep. Discard it.
                with contextlib.suppress(Exception):
                    await (await lock_session.connection()).invalidate()
                raise


async def _run_sweep_body(
    *,
    db_factory: async_sessionmaker[AsyncSession],
    redis_client: object,
) -> int:
    settings = get_settings()
    total = 0
    # Each sweep is isolated: one failing sweep must not skip the others.
    try:
        repaired, dead_lettered = await sweep_session_thread_binding_drift(db_factory)
        total += repaired + dead_lettered
    except Exception:
        _sweep_failed("thread_binding_drift")
    sweeps: list[tuple[str, Callable[[], Awaitable[int]]]] = [
        ("creating_watchdog", lambda: sweep_creating_watchdog(db_factory)),
        ("resuming_watchdog", lambda: sweep_resuming_watchdog(db_factory)),
        (
            "stopping_watchdog",
            lambda: sweep_stopping_watchdog(db_factory, settings.STOP_STUCK_THRESHOLD_SECONDS),
        ),
        (
            "idle_timeout",
            lambda: sweep_idle_timeout(db_factory, settings.DEFAULT_IDLE_TIMEOUT_MINUTES),
        ),
        (
            "archive_timeout",
            lambda: sweep_archive_timeout(db_factory, settings.DEFAULT_ARCHIVE_TIMEOUT_MINUTES),
        ),
        (
            "stop_timeout",
            lambda: sweep_stop_timeout(db_factory, settings.DEFAULT_STOP_TIMEOUT_HOURS),
        ),
        (
            "heartbeat_orphans",
            lambda: sweep_runner_heartbeat_orphans(db_factory, settings.ORPHAN_RECOVERY_TTL_HOURS),
        ),
        ("idempotency_keys", lambda: sweep_idempotency_keys(db_factory)),
        ("events", lambda: sweep_events(db_factory, settings.EVENT_RETENTION_DAYS)),
        ("outbox", lambda: sweep_outbox(db_factory, settings.OUTBOX_RETENTION_DAYS)),
        ("turn_results", lambda: sweep_turn_results(db_factory)),
        ("dead_letter_gauges", lambda: scan_input_dead_letters(redis_client, db_factory)),
    ]
    for name, sweep in sweeps:
        try:
            total += await sweep()
        except Exception:
            _sweep_failed(name)
    if hasattr(redis_client, "scan_iter"):
        try:
            for pattern in ("runner:jobs:*", "input:jobs:*", "discord:outbound"):
                async for stream in redis_client.scan_iter(match=pattern):
                    await trim_acknowledged(
                        redis_client, stream, settings.REDIS_STREAM_RETENTION_HOURS
                    )
        except Exception:
            _sweep_failed("stream_trim")
    REGISTRY.set_gauge("discode_janitor_last_sweep_timestamp", time.time())
    return total


def _sweep_failed(name: str) -> None:
    logger.exception("janitor sweep %s failed", name)
    REGISTRY.increment("discode_janitor_sweep_errors_total")


async def run_janitor() -> None:
    """Janitor main loop with concurrent outbox relay."""
    logger.info("janitor starting")
    runtime = AppRuntime.from_env()
    runtime.start_metrics(runtime.settings.JANITOR_METRICS_BIND)
    interval_seconds = runtime.settings.JANITOR_SWEEP_INTERVAL_SECONDS
    relay = RelayLoop(
        producer="janitor",
        redis_client=runtime.redis,
        db_factory=runtime.db_factory,
    )
    relay_task = asyncio.create_task(supervise_relay(relay), name="janitor-relay")
    try:
        while True:
            try:
                await run_sweep_once(
                    db_factory=runtime.db_factory,
                    redis_client=runtime.redis,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("janitor sweep error")
            await asyncio.sleep(interval_seconds)
    finally:
        relay_task.cancel()
        await asyncio.gather(relay_task, return_exceptions=True)
        await runtime.close()


def main() -> None:
    from discode.log import configure_logging

    configure_logging()
    try:
        asyncio.run(run_janitor())
    except KeyboardInterrupt:
        logger.info("janitor stopped")
