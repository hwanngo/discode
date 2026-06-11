from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from discode.janitor.main import (
    _run_sweep_body,
    run_janitor,
    run_sweep_once,
    supervise_relay,
)


@pytest.mark.asyncio
async def test_run_sweep_once_sums_all_sweeps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("discode.janitor.main.sweep_creating_watchdog", AsyncMock(return_value=1))
    monkeypatch.setattr("discode.janitor.main.sweep_resuming_watchdog", AsyncMock(return_value=2))
    monkeypatch.setattr("discode.janitor.main.sweep_idle_timeout", AsyncMock(return_value=3))
    monkeypatch.setattr("discode.janitor.main.sweep_archive_timeout", AsyncMock(return_value=4))
    monkeypatch.setattr("discode.janitor.main.sweep_stop_timeout", AsyncMock(return_value=5))
    monkeypatch.setattr(
        "discode.janitor.main.sweep_runner_heartbeat_orphans",
        AsyncMock(return_value=6),
    )
    monkeypatch.setattr("discode.janitor.main.sweep_idempotency_keys", AsyncMock(return_value=7))
    monkeypatch.setattr("discode.janitor.main.sweep_events", AsyncMock(return_value=9))
    monkeypatch.setattr("discode.janitor.main.sweep_outbox", AsyncMock(return_value=8))
    monkeypatch.setattr("discode.janitor.main.scan_input_dead_letters", AsyncMock(return_value=10))
    monkeypatch.setattr(
        "discode.janitor.main.sweep_session_thread_binding_drift",
        AsyncMock(return_value=(0, 0)),
    )

    total = await _run_sweep_body(db_factory=AsyncMock(), redis_client=object())

    assert total == 55


def _cm_db_factory() -> object:
    """A db_factory whose call returns an async context manager yielding a session."""

    @asynccontextmanager
    async def _ctx():  # type: ignore[no-untyped-def]
        yield AsyncMock()

    return lambda: _ctx()


@pytest.mark.asyncio
async def test_run_sweep_once_skips_when_lock_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second concurrent janitor that fails to acquire the advisory lock skips
    the sweep pass (returns -1) and does not run the sweep body."""
    monkeypatch.setattr(
        "discode.janitor.main.acquire_advisory_lock", AsyncMock(return_value=False)
    )
    release_mock = AsyncMock()
    monkeypatch.setattr("discode.janitor.main.release_advisory_lock", release_mock)
    body_mock = AsyncMock(return_value=99)
    monkeypatch.setattr("discode.janitor.main._run_sweep_body", body_mock)

    result = await run_sweep_once(db_factory=_cm_db_factory(), redis_client=object())

    assert result == -1
    body_mock.assert_not_awaited()
    # No lock acquired → nothing to release.
    release_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_sweep_once_runs_and_releases_when_lock_acquired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the advisory lock is acquired, the body runs and the lock is released."""
    monkeypatch.setattr(
        "discode.janitor.main.acquire_advisory_lock", AsyncMock(return_value=True)
    )
    release_mock = AsyncMock()
    monkeypatch.setattr("discode.janitor.main.release_advisory_lock", release_mock)
    body_mock = AsyncMock(return_value=42)
    monkeypatch.setattr("discode.janitor.main._run_sweep_body", body_mock)

    result = await run_sweep_once(db_factory=_cm_db_factory(), redis_client=object())

    assert result == 42
    body_mock.assert_awaited_once()
    release_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_janitor_runs_sweep_and_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = AsyncMock()
    runtime.db_factory = AsyncMock()
    runtime.redis = AsyncMock()
    runtime.close = AsyncMock()
    monkeypatch.setattr("discode.janitor.main.AppRuntime.from_env", lambda: runtime)

    run_sweep_once_mock = AsyncMock(return_value=0)
    monkeypatch.setattr("discode.janitor.main.run_sweep_once", run_sweep_once_mock)

    relay_started = asyncio.Event()
    relay_cancelled = asyncio.Event()

    class FakeRelayLoop:
        def __init__(self, producer: str, redis_client: object, db_factory: object) -> None:
            assert producer == "janitor"
            assert redis_client is runtime.redis
            assert db_factory is runtime.db_factory

        async def run(self) -> None:
            relay_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                relay_cancelled.set()
                raise

    monkeypatch.setattr("discode.janitor.main.RelayLoop", FakeRelayLoop)
    monkeypatch.setattr("discode.janitor.main.os.environ.get", lambda _k, _d: "0")

    sleep_calls = 0

    async def fake_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            await relay_started.wait()
            return
        raise asyncio.CancelledError

    monkeypatch.setattr("discode.janitor.main.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_janitor()

    run_sweep_once_mock.assert_awaited()
    assert relay_cancelled.is_set()
    runtime.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_supervise_relay_restarts_after_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """supervise_relay restarts the relay after run() raises once, then exits cleanly."""
    calls = 0

    class FlakyRelay:
        async def run(self) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient DB blip")
            # Second invocation returns cleanly → supervisor stops.
            return

    # Skip the backoff sleep so the test is fast.
    monkeypatch.setattr("discode.janitor.main.asyncio.sleep", AsyncMock())

    await supervise_relay(FlakyRelay())  # type: ignore[arg-type]

    assert calls == 2


@pytest.mark.asyncio
async def test_supervise_relay_propagates_cancellation() -> None:
    """supervise_relay re-raises CancelledError so shutdown works."""

    class CancellingRelay:
        async def run(self) -> None:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await supervise_relay(CancellingRelay())  # type: ignore[arg-type]
