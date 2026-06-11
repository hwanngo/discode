"""Pure-logic tests for FIX 6: run_stop_saga / run_resume_saga must write a
runner envelope (runner.stop_session.v1 / runner.resume_session.v1) in the same
transaction as the DB state change. Otherwise the runner never learns and the
session_store entry leaks (stop) / input is dropped (resume).
"""

from __future__ import annotations

import contextlib
import types
import uuid

import pytest

import discode.bot.session_service as svc
from discode.bot.session_service import run_resume_saga, run_stop_saga


class _FakeUpdateResult:
    rowcount = 1


def _fake_session(sess_row, captured):
    async def _get(_model, _pk):
        return sess_row

    async def _execute(*_a, **_k):
        return _FakeUpdateResult()

    def _add(_obj):
        pass

    return types.SimpleNamespace(get=_get, execute=_execute, add=_add)


def _fake_db_factory(sess_row, captured):
    fake = _fake_session(sess_row, captured)

    @contextlib.asynccontextmanager
    async def _begin():
        yield fake

    fake.begin = _begin

    @contextlib.asynccontextmanager
    async def _ctx():
        yield fake

    return lambda: _ctx()


def _patch_write_outbox(monkeypatch, captured):
    async def _fake(db, *, producer, stream_key, envelope_type, payload):
        captured.append(
            {"stream_key": stream_key, "envelope_type": envelope_type, "payload": payload}
        )
        return None

    monkeypatch.setattr(svc, "write_outbox", _fake)


@pytest.mark.asyncio
async def test_stop_saga_writes_runner_stop_envelope(monkeypatch):
    captured: list[dict] = []
    sid = uuid.uuid4()
    sess_row = types.SimpleNamespace(
        guild_id="g1", status="running", host_id="host-9", thread_id="t1"
    )
    _patch_write_outbox(monkeypatch, captured)

    ok = await run_stop_saga(
        _fake_db_factory(sess_row, captured),
        session_id=str(sid),
        guild_id="g1",
        requested_by_id="u1",
    )
    assert ok is True
    envs = [c for c in captured if c["envelope_type"] == "runner.stop_session.v1"]
    assert len(envs) == 1
    env = envs[0]
    assert env["stream_key"] == "runner:jobs:host-9"
    assert env["payload"]["session_id"] == str(sid)
    assert env["payload"]["type"] == "runner.stop_session.v1"


@pytest.mark.asyncio
async def test_resume_saga_writes_runner_resume_envelope(monkeypatch):
    captured: list[dict] = []
    sid = uuid.uuid4()
    sess_row = types.SimpleNamespace(
        guild_id="g1", status="archived", host_id="host-3", thread_id="t-77"
    )
    _patch_write_outbox(monkeypatch, captured)

    ok = await run_resume_saga(
        _fake_db_factory(sess_row, captured),
        session_id=str(sid),
        guild_id="g1",
        host_id="host-3",
    )
    assert ok is True
    envs = [c for c in captured if c["envelope_type"] == "runner.resume_session.v1"]
    assert len(envs) == 1
    env = envs[0]
    assert env["stream_key"] == "runner:jobs:host-3"
    assert env["payload"]["session_id"] == str(sid)
    assert env["payload"]["guild_id"] == "g1"
    assert env["payload"]["thread_id"] == "t-77"
    assert env["payload"]["type"] == "runner.resume_session.v1"
