"""FIX 4: create saga must NOT emit 'running' when the status UPDATE matched
zero rows (janitor flipped the session to 'failed' between the two TXs).

This is a pure-logic test: it drives run_create_job with a mocked db_factory /
AsyncSession so it needs no DB / docker. We stub the lock TX to report the
session as 'creating' (passes the gate), allowed-root validation to pass, then
make the steps-6-8 UPDATE report rowcount=0 and assert no Event is added and no
outbox write happens.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from discode.runner.saga_create import CreateJobPayload, run_create_job


def _payload() -> CreateJobPayload:
    import uuid

    return CreateJobPayload(
        session_id=str(uuid.uuid4()),
        guild_id="g1",
        owner_id="o1",
        tool="claude",
        cwd_realpath="/tmp/work",
        thread_id="t1",
        hook_secret_ciphertext="ct",
        idempotency_key="k",
    )


class _FakeResult:
    def __init__(self, *, scalar=None, rowcount=1):
        self._scalar = scalar
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self._scalar


@pytest.mark.asyncio
async def test_running_skipped_when_update_rowcount_zero():
    payload = _payload()

    # Mock session whose row is in 'creating' so the gate passes.
    fake_session_row = MagicMock()
    fake_session_row.status = "creating"

    added: list = []

    # The steps-6-8 UPDATE returns rowcount=0 (lost the row).
    async def execute(stmt, *a, **k):
        # First call in lock TX = SELECT ... FOR UPDATE -> session row.
        # Distinguish by call order via a counter on the mock.
        execute.calls += 1
        if execute.calls == 1:
            return _FakeResult(scalar=fake_session_row)
        # Subsequent executes are the UPDATE (rowcount 0) etc.
        return _FakeResult(rowcount=0)

    execute.calls = 0

    fake_db = MagicMock()
    fake_db.execute = AsyncMock(side_effect=execute)
    fake_db.add = lambda obj: added.append(obj)

    @asynccontextmanager
    async def _begin():
        yield

    fake_db.begin = _begin

    @asynccontextmanager
    async def _factory():
        yield fake_db

    db_factory = MagicMock(side_effect=lambda: _factory())

    # revalidate_cwd must pass (return a realpath, no deny reason).
    with patch(
        "discode.runner.saga_create.revalidate_cwd",
        new=AsyncMock(return_value=("/tmp/work", None)),
    ), patch(
        "discode.runner.saga_create.get_adapter",
        new=AsyncMock(return_value=(MagicMock(env_allowlist=lambda: [], extra_paths=lambda: []),
                                    MagicMock())),
    ), patch(
        "discode.runner.saga_create.write_outbox", new=AsyncMock()
    ) as mock_outbox:
        store: dict = {}
        await run_create_job(
            payload, host_id="h1", db_factory=db_factory, session_store=store
        )

    # No audit Event added, no outbox enqueue, store not populated.
    assert added == []
    mock_outbox.assert_not_called()
    assert payload.session_id not in store
