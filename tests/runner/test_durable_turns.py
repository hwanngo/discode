from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from discode.runner import saga_input


class Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def first(self):
        return self.value


class DB:
    def __init__(self, turn=None, status="running", earliest="key"):
        self.turn = turn
        self.status = status
        self.earliest = earliest
        self.writes = []

    async def execute(self, query, params=None):
        sql = str(query)
        if "FROM sessions WHERE" in sql:
            return Result(
                dict(
                    tool="test",
                    cwd_realpath="/tmp",
                    tool_resume_token=None,
                    guild_id="guild",
                    thread_id="thread",
                    host_id="host",
                    status=self.status,
                    owner_id="owner",
                    lifecycle_version=2,
                )
            )
        if "SELECT * FROM input_turns" in sql:
            return Result(self.turn)
        self.writes.append((sql, params))
        return Result(None)

    async def commit(self):
        pass

    async def close(self):
        pass

    async def scalar(self, query, params=None):
        return self.earliest

    @asynccontextmanager
    async def begin(self):
        yield

    @asynccontextmanager
    async def factory(self):
        yield self


@pytest.fixture
def payload():
    return saga_input.InputJobPayload("session", "guild", "thread", "host", "input", "key", 2)


@pytest.mark.asyncio
async def test_completed_delivery_retries_without_execution(monkeypatch, payload):
    db = DB(
        dict(session_id="session", lifecycle_version=2, status="completed", result_text="saved")
    )
    execute = AsyncMock()
    client = SimpleNamespace(post_reply=AsyncMock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr(saga_input, "_deliver", execute)
    monkeypatch.setattr(saga_input, "get_reply_client", lambda: client)
    assert await saga_input._run_locked(payload, {}, db.factory) == "deferred"
    client.post_reply.side_effect = None
    assert await saga_input._run_locked(payload, {}, db.factory) == "delivered"
    execute.assert_not_called()
    assert client.post_reply.await_count == 2
    assert any((params or {}).get("status") == "delivered" for _, params in db.writes)


@pytest.mark.asyncio
async def test_crashed_execution_is_uncertain_never_reexecuted(monkeypatch, payload):
    db = DB(dict(session_id="session", lifecycle_version=2, status="executing"))
    monkeypatch.setattr(
        saga_input, "get_reply_client", lambda: SimpleNamespace(post_reply=AsyncMock())
    )
    execute = AsyncMock()
    monkeypatch.setattr(saga_input, "_deliver", execute)
    assert await saga_input._run_locked(payload, {}, db.factory) == "aborted"
    execute.assert_not_called()
    assert any("status='uncertain'" in sql for sql, _ in db.writes)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["archived", "stopping", "stopped", "failed", "resuming"])
async def test_cached_context_cannot_bypass_terminal_status(payload, status):
    db = DB(status=status)
    assert (
        await saga_input._run_locked(payload, {"session": {"status": "running"}}, db.factory)
        == "aborted"
    )


@pytest.mark.asyncio
async def test_old_lifecycle_and_other_guild_rejected(payload):
    db = DB()
    payload.lifecycle_version = 1
    assert await saga_input._run_locked(payload, {}, db.factory) == "aborted"
    payload.lifecycle_version = 2
    payload.guild_id = "other"
    assert await saga_input._run_locked(payload, {}, db.factory) == "aborted"


@pytest.mark.asyncio
async def test_later_turn_cannot_overtake_earlier(payload):
    db = DB(earliest="earlier-key")
    assert await saga_input._run_locked(payload, {}, db.factory) == "deferred"


@pytest.mark.asyncio
async def test_real_store_builder_loads_overrides_by_payload_id(monkeypatch):
    from discode.runner.session_store import build_session_store_entry

    entry = build_session_store_entry(
        tool="test",
        cwd="/tmp",
        env={"PATH": "/usr/bin"},
        tool_resume_token=None,
        guild_id="g",
        thread_id="t",
        host_id="h",
        status="running",
    )
    payload = saga_input.InputJobPayload(
        "00000000-0000-0000-0000-000000000001", "g", "t", "h", "hello", "key"
    )
    adapter = SimpleNamespace(
        exec_cmd=lambda *a, **k: ["tool", "hello"],
        extra_paths=lambda: [],
        exec_uses_pty=lambda: False,
    )
    tool = SimpleNamespace(
        argv_prefix=["tool"], model_flag="--model", name="test", api_key_env=None, base_url_env=None
    )
    monkeypatch.setattr(saga_input, "get_adapter", AsyncMock(return_value=(adapter, tool)))
    db = SimpleNamespace(
        scalars=AsyncMock(
            return_value=SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(key="model", value="chosen-model", value_ciphertext=None)
                ]
            )
        )
    )
    execution = await saga_input.build_exec_session(entry, payload, db)
    assert execution._argv[1:3] == ["--model", "chosen-model"]
    query = db.scalars.call_args.args[0]
    assert payload.session_id in query.compile().params.values()


@pytest.mark.asyncio
async def test_permanently_undeliverable_reply_retires_turn(monkeypatch, payload):
    """A 410 (deleted thread, lost access) marks the turn terminal instead of looping."""
    from discode.runner.reply_client import ReplyError

    db = DB(
        dict(session_id="session", lifecycle_version=2, status="completed", result_text="saved")
    )
    client = SimpleNamespace(post_reply=AsyncMock(side_effect=ReplyError(410, "gone")))
    monkeypatch.setattr(saga_input, "get_reply_client", lambda: client)
    assert await saga_input._run_locked(payload, {}, db.factory) == "aborted"
    assert any((params or {}).get("status") == "delivery_failed" for _, params in db.writes)


@pytest.mark.asyncio
async def test_transient_reply_error_still_defers(monkeypatch, payload):
    from discode.runner.reply_client import ReplyError

    db = DB(
        dict(session_id="session", lifecycle_version=2, status="completed", result_text="saved")
    )
    client = SimpleNamespace(post_reply=AsyncMock(side_effect=ReplyError(503, "stop")))
    monkeypatch.setattr(saga_input, "get_reply_client", lambda: client)
    assert await saga_input._run_locked(payload, {}, db.factory) == "deferred"
    assert not any((params or {}).get("status") == "delivery_failed" for _, params in db.writes)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["delivery_failed", "aborted"])
async def test_redelivered_retired_turn_is_not_reposted(monkeypatch, payload, status):
    """Review 2026-10-12: retired turns are terminal even if their XACK was lost."""
    db = DB(dict(session_id="session", lifecycle_version=2, status=status, result_text=None))
    client = SimpleNamespace(post_reply=AsyncMock())
    monkeypatch.setattr(saga_input, "get_reply_client", lambda: client)
    assert await saga_input._run_locked(payload, {}, db.factory) == "skipped_replay"
    client.post_reply.assert_not_called()


@pytest.mark.asyncio
async def test_reply_rejected_as_invalid_retires_turn(monkeypatch, payload):
    from discode.runner.reply_client import ReplyError

    db = DB(
        dict(session_id="session", lifecycle_version=2, status="completed", result_text="saved")
    )
    client = SimpleNamespace(post_reply=AsyncMock(side_effect=ReplyError(422, "too long")))
    monkeypatch.setattr(saga_input, "get_reply_client", lambda: client)
    assert await saga_input._run_locked(payload, {}, db.factory) == "aborted"
    assert any((params or {}).get("status") == "delivery_failed" for _, params in db.writes)
