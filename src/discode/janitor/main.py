from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
)
from discode.janitor.timeouts import (
    sweep_archive_timeout,
    sweep_creating_watchdog,
    sweep_idle_timeout,
    sweep_resuming_watchdog,
    sweep_stop_timeout,
)
from discode.queues.relay import RelayLoop
from discode.runtime import AppRuntime

logger = logging.getLogger(__name__)

# Backoff bounds (seconds) for restarting the relay after an unexpected crash.
RELAY_RESTART_BASE_BACKOFF_SECONDS = 1.0
RELAY_RESTART_MAX_BACKOFF_SECONDS = 30.0


async def supervise_relay(relay: RelayLoop) -> None:
    """Run ``relay.run()`` forever, restarting it with backoff if it crashes.

    ``RelayLoop.run()`` re-raises on non-row errors (e.g. a transient DB blip).
    Without supervision a single transient failure would silently kill the relay
    and leave outbox rows unpublished forever. This wrapper logs the exception and
    restarts the relay, applying exponential backoff so a persistently-failing
    relay does not hot-loop. ``CancelledError`` propagates so shutdown still works.
    """
    backoff = RELAY_RESTART_BASE_BACKOFF_SECONDS
    while True:
        try:
            await relay.run()
            # run() returned without raising — it only does so when stopped
            # intentionally; treat as a clean exit and stop supervising.
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("relay crashed; restarting in %.1fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RELAY_RESTART_MAX_BACKOFF_SECONDS)


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
            return await _run_sweep_body(
                db_factory=db_factory, redis_client=redis_client
            )
        finally:
            await release_advisory_lock(lock_session, JANITOR_SWEEP_LOCK_ID)


async def _run_sweep_body(
    *,
    db_factory: async_sessionmaker[AsyncSession],
    redis_client: object,
) -> int:
    repaired, dead_lettered = await sweep_session_thread_binding_drift(db_factory)
    counts = [
        await sweep_creating_watchdog(db_factory),
        await sweep_resuming_watchdog(db_factory),
        await sweep_idle_timeout(db_factory),
        await sweep_archive_timeout(db_factory),
        await sweep_stop_timeout(db_factory),
        await sweep_runner_heartbeat_orphans(db_factory),
        await sweep_idempotency_keys(db_factory),
        await sweep_events(db_factory),
        await sweep_outbox(db_factory),
        await scan_input_dead_letters(redis_client, db_factory),
        repaired,
        dead_lettered,
    ]
    return sum(counts)


async def run_janitor() -> None:
    """Janitor main loop with concurrent outbox relay."""
    logger.info("janitor starting")
    runtime = AppRuntime.from_env()
    interval_seconds = float(os.environ.get("JANITOR_SWEEP_INTERVAL_SECONDS", "60"))
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
    try:
        asyncio.run(run_janitor())
    except KeyboardInterrupt:
        logger.info("janitor stopped")
