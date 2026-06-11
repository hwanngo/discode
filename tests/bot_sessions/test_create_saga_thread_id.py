"""Pure-logic test for FIX 5: run_create_saga must write the real thread_id into
the create outbox payload up front (no bind-after-publish race).

We stub the DB layer and capture the payload passed to write_outbox.
"""

from __future__ import annotations

import contextlib

import pytest
from cryptography.fernet import Fernet

import discode.bot.sagas.create as create_mod
from discode.bot.sagas.create import run_create_saga

TEST_KEY = Fernet.generate_key().decode()


class _FakeResult:
    def fetchone(self):
        return None  # no archived session to resume from


class _FakeSession:
    async def execute(self, *_a, **_k):
        return _FakeResult()

    def add(self, *_a, **_k):
        pass

    async def flush(self):
        pass

    async def commit(self):
        pass

    @contextlib.asynccontextmanager
    async def begin(self):
        yield self


class _FakeDBFactory:
    @contextlib.asynccontextmanager
    async def _ctx(self):
        yield _FakeSession()

    def __call__(self):
        return self._ctx()


@pytest.mark.asyncio
async def test_create_saga_payload_has_real_thread_id(monkeypatch):
    captured = {}

    async def _fake_write_outbox(db, *, producer, stream_key, envelope_type, payload):
        captured["payload"] = payload
        captured["envelope_type"] = envelope_type
        return None

    monkeypatch.setattr(create_mod, "write_outbox", _fake_write_outbox)

    result = await run_create_saga(
        _FakeDBFactory(),
        guild_id="g1",
        owner_id="u1",
        owner_username="user",
        tool="claude",
        name="sess",
        cwd="/home/user/project",
        cwd_realpath="/home/user/project",
        parent_channel_id="ch1",
        runner_host_id="host1",
        deployment_secret_key=TEST_KEY,
        thread_id="thread-12345",
    )

    assert result.session_id
    assert captured["envelope_type"] == "runner.create_session.v1"
    # The crucial assertion: real thread_id is in the payload, not "".
    assert captured["payload"]["thread_id"] == "thread-12345"
