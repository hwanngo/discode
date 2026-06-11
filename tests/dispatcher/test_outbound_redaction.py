"""FIX 1/2/3 tests: outbound redaction, enforce_nonce dedup, system-notice idempotency."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from discode.dispatcher.discord_client import DISCORD_API_BASE, DiscordRestClient
from discode.dispatcher.main import _handle_system_notice
from tests.dispatcher.conftest import FakeSession

BASE = DISCORD_API_BASE


# ---------------------------------------------------------------------------
# FIX 1: dispatcher redacts outbound text at the single send chokepoint
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dispatcher_send_chokepoint_redacts_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dispatcher send chokepoint must redact secrets in outbound text
    before they reach the (fake) Discord client."""
    from discode.dispatcher import main as dispatcher_main

    secret_db_url = "postgresql://admin:hunter2@db.internal:5432/prod"
    secret_token = "MTk4NzY1NDMyMTA5ODc2NTQz.GhI_jk.aBcDeFgHiJkLmNoPqRsTuVwXyZ012345678"
    message = f"connecting to {secret_db_url} with token {secret_token}"

    sent_content: list[str] = []

    class FakeClient:
        async def start(self) -> None:
            pass

        async def close(self) -> None:
            pass

        def set_retry_after_callback(self, cb) -> None:  # noqa: ANN001
            pass

        async def send_message(
            self, *, thread_id: str, content: str, nonce: str | None = None
        ) -> str:
            sent_content.append(content)
            return "fake-msg-id"

        async def archive_thread(self, *, thread_id: str) -> None:
            pass

    fake_client = FakeClient()
    fake_runtime = MagicMock()
    fake_runtime.settings.DISCORD_TOKEN = "Bot fake-token"
    fake_runtime.db_factory = _FakeDB({"ikeys": {}, "sessions": {}})
    fake_runtime.close = AsyncMock()

    envelopes = [
        (
            "1-0",
            {
                "envelope_type": "discord.system_notice.v1",
                "payload": json.dumps(
                    {
                        "type": "discord.system_notice.v1",
                        "thread_id": "t1",
                        "message": message,
                        "idempotency_key": "k1",
                    }
                ),
            },
        ),
    ]
    consumer = AsyncMock()
    consumer.ensure_group = AsyncMock()
    consumer.claim_pending = AsyncMock(return_value=[])
    consumer.read_next = AsyncMock(side_effect=[envelopes, asyncio.CancelledError])
    consumer.ack = AsyncMock()

    with (
        patch.object(dispatcher_main, "AppRuntime") as mock_runtime_cls,
        patch.object(dispatcher_main, "DiscordRestClient", return_value=fake_client),
        patch.object(dispatcher_main, "DispatcherConsumer", return_value=consumer),
    ):
        mock_runtime_cls.from_env.return_value = fake_runtime
        with pytest.raises((asyncio.CancelledError, Exception)):
            await dispatcher_main.run_dispatcher()

    assert sent_content, "send_message was never called"
    out = sent_content[0]
    assert "hunter2" not in out
    assert secret_token not in out
    assert "[REDACTED]" in out


# ---------------------------------------------------------------------------
# FIX 2: enforce_nonce is set on the POST body when a nonce is present
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_send_message_sets_enforce_nonce_with_nonce() -> None:
    c = DiscordRestClient(token="Bot test-token")
    c._session = FakeSession()  # type: ignore[assignment]
    fake = c._session
    try:
        fake.set_response("POST", f"{BASE}/channels/900/messages", payload={"id": "1"})  # type: ignore[attr-defined]
        await c.send_message(thread_id="900", content="hi", nonce="n-1")
    finally:
        await c.close()

    sent = fake.json_for("POST", f"{BASE}/channels/900/messages")  # type: ignore[attr-defined]
    assert sent.get("nonce") == "n-1"
    assert sent.get("enforce_nonce") is True


@pytest.mark.asyncio
async def test_send_message_no_enforce_nonce_without_nonce() -> None:
    c = DiscordRestClient(token="Bot test-token")
    c._session = FakeSession()  # type: ignore[assignment]
    fake = c._session
    try:
        fake.set_response("POST", f"{BASE}/channels/901/messages", payload={"id": "1"})  # type: ignore[attr-defined]
        await c.send_message(thread_id="901", content="hi", nonce=None)
    finally:
        await c.close()

    captured = [fake.json_for("POST", f"{BASE}/channels/901/messages")]  # type: ignore[attr-defined]

    assert "enforce_nonce" not in captured[0]


# ---------------------------------------------------------------------------
# FIX 3: system notices use DB idempotency (claim-then-finalize)
# ---------------------------------------------------------------------------
class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeDB:
    def __init__(self, store: dict):
        self._store = store

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    def begin(self):
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        return txn

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        params = params or {}
        keys = self._store["ikeys"]
        if sql.startswith("INSERT INTO idempotency_keys"):
            key = (params["etype"], params["ikey"])
            if key in keys:
                return _FakeResult(None)
            keys[key] = {"outcome": None}
            return _FakeResult(("row",))
        if sql.startswith("UPDATE idempotency_keys"):
            key = (params["etype"], params["ikey"])
            if key in keys:
                keys[key]["outcome"] = "done"
                return _FakeResult(("row",))
            return _FakeResult(None)
        if sql.startswith("SELECT outcome FROM idempotency_keys"):
            key = (params["etype"], params["ikey"])
            if key in keys:
                return _FakeResult((keys[key]["outcome"],))
            return _FakeResult(None)
        return _FakeResult(None)


@pytest.mark.asyncio
async def test_system_notice_skips_genuine_duplicate() -> None:
    store = {"ikeys": {}, "sessions": {}}
    db = _FakeDB(store)
    envelope = {
        "thread_id": "t1",
        "message": "hello",
        "idempotency_key": "k1",
        "session_id": "s1",
    }

    send1 = AsyncMock(return_value="msg-id")
    await _handle_system_notice(envelope, send_to_discord=send1, db_factory=db)
    send1.assert_awaited_once()

    send2 = AsyncMock(return_value="msg-id")
    await _handle_system_notice(envelope, send_to_discord=send2, db_factory=db)
    send2.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_notice_retries_after_failure() -> None:
    store = {"ikeys": {}, "sessions": {}}
    db = _FakeDB(store)
    envelope = {
        "thread_id": "t1",
        "message": "hello",
        "idempotency_key": "k1",
        "session_id": "s1",
    }

    failing = AsyncMock(side_effect=RuntimeError("discord 503"))
    with pytest.raises(RuntimeError):
        await _handle_system_notice(envelope, send_to_discord=failing, db_factory=db)

    good = AsyncMock(return_value="msg-id")
    await _handle_system_notice(envelope, send_to_discord=good, db_factory=db)
    good.assert_awaited_once()
