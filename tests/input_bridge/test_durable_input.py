"""PostgreSQL integration checks of the actual turn handler, not copied claim SQL."""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from discode.runner import saga_input
from tests.input_bridge.test_stage2_claim import _seed_session, db_factory_fixture  # noqa: F401


@pytest.mark.asyncio
async def test_advisory_lock_defers_other_consumer(db_factory_fixture):  # noqa: F811
    factory = db_factory_fixture
    sid = await _seed_session(factory)
    payload = saga_input.InputJobPayload(sid, "g", "t", "h", "hello", str(uuid.uuid4()))
    async with factory() as owner, owner.begin():
        await owner.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:sid,0))"), {"sid": sid}
        )
        assert (
            await saga_input.run_input_job(payload, session_store={}, db_factory=factory)
            == "deferred"
        )


@pytest.mark.asyncio
async def test_saved_output_delivery_retry_does_not_execute(db_factory_fixture, monkeypatch):  # noqa: F811
    factory = db_factory_fixture
    sid = await _seed_session(factory)
    key = str(uuid.uuid4())
    async with factory() as db, db.begin():
        row = (
            (await db.execute(text("SELECT * FROM sessions WHERE id=:sid"), {"sid": sid}))
            .mappings()
            .one()
        )
        await db.execute(
            text("""
            INSERT INTO input_turns(idempotency_key,session_id,lifecycle_version,status,result_text)
            VALUES (:key,:sid,0,'completed','persisted answer')
        """),
            {"key": key, "sid": sid},
        )
    payload = saga_input.InputJobPayload(
        sid, row["guild_id"], row["thread_id"], row["host_id"], "hello", key, 0
    )
    reply = AsyncMock()
    reply.post_reply.side_effect = RuntimeError("offline")
    monkeypatch.setattr(saga_input, "get_reply_client", lambda: reply)
    execution = AsyncMock()
    monkeypatch.setattr(saga_input, "_deliver", execution)
    assert (
        await saga_input.run_input_job(payload, session_store={}, db_factory=factory) == "deferred"
    )
    reply.post_reply.side_effect = None
    assert (
        await saga_input.run_input_job(payload, session_store={}, db_factory=factory) == "delivered"
    )
    assert (
        await saga_input.run_input_job(payload, session_store={}, db_factory=factory)
        == "skipped_replay"
    )
    execution.assert_not_awaited()
    async with factory() as db:
        assert (
            await db.scalar(
                text("SELECT status FROM input_turns WHERE idempotency_key=:key"), {"key": key}
            )
            == "delivered"
        )


@pytest.mark.asyncio
async def test_chunk_checkpoint_skips_delivered_and_retries_failed(db_factory_fixture):  # noqa: F811
    from discode.runner.control_api import _send_checkpointed

    factory = db_factory_fixture
    sid = await _seed_session(factory)
    first = AsyncMock(return_value="message-1")
    second = AsyncMock(side_effect=RuntimeError("offline"))
    key = str(uuid.uuid4())
    assert await _send_checkpointed(factory, sid, key + ":1", first) == "message-1"
    with pytest.raises(RuntimeError):
        await _send_checkpointed(factory, sid, key + ":2", second)
    assert await _send_checkpointed(factory, sid, key + ":1", first) == "message-1"
    first.assert_awaited_once()
    second.side_effect = None
    second.return_value = "message-2"
    assert await _send_checkpointed(factory, sid, key + ":2", second) == "message-2"
    assert second.await_count == 2


@pytest.mark.asyncio
async def test_restart_rehydrates_only_current_version(db_factory_fixture, monkeypatch, tmp_path):  # noqa: F811
    import json
    from types import SimpleNamespace

    from discode.db.models import AllowedRoot
    from discode.runner.main import dispatch_runner_job
    from discode.tools import registry

    factory = db_factory_fixture
    sid = await _seed_session(factory)
    async with factory() as db, db.begin():
        row = (
            (await db.execute(text("SELECT * FROM sessions WHERE id=:sid"), {"sid": sid}))
            .mappings()
            .one()
        )
        await db.execute(
            text("""
            UPDATE sessions SET status='resuming', lifecycle_version=1, tool_resume_token=NULL,
                cwd_realpath=:cwd
            WHERE id=:sid
        """),
            {"sid": sid, "cwd": str(tmp_path.resolve())},
        )
        db.add(
            AllowedRoot(
                guild_id=row["guild_id"],
                user_id=row["owner_id"],
                runner_host_id=row["host_id"],
                path=str(tmp_path),
                realpath=str(tmp_path.resolve()),
                created_by=row["owner_id"],
            )
        )
    monkeypatch.setattr(
        registry,
        "get_adapter",
        AsyncMock(return_value=(SimpleNamespace(mint_initial_token=lambda: "fresh-token"), None)),
    )
    store = {sid: {"tool_resume_token": "old-token"}}
    envelope = {
        "envelope_type": "runner.restart_session.v1",
        "payload": json.dumps(
            {
                "session_id": sid,
                "guild_id": row["guild_id"],
                "lifecycle_version": 1,
                "thread_id": row["thread_id"],
            }
        ),
    }
    assert await dispatch_runner_job(
        envelope, host_id=row["host_id"], db_factory=factory, session_store=store
    )
    assert store[sid]["tool_resume_token"] == "fresh-token"
    async with factory() as db:
        state = (
            await db.execute(
                text("SELECT status,tool_resume_token FROM sessions WHERE id=:sid"), {"sid": sid}
            )
        ).one()
        assert tuple(state) == ("running", "fresh-token")
