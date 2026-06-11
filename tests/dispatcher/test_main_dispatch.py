from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from discode.dispatcher.main import _consume_dispatcher_stream, dispatch_discord_envelope


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeIdemDB:
    """In-memory fake of the idempotency_keys SQL the system_notice gate uses."""

    def __init__(self) -> None:
        self._keys: dict[tuple[str, str], dict] = {}

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    def begin(self):
        from unittest.mock import AsyncMock, MagicMock

        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        return txn

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        params = params or {}
        if sql.startswith("INSERT INTO idempotency_keys"):
            key = (params["etype"], params["ikey"])
            if key in self._keys:
                return _FakeResult(None)
            self._keys[key] = {"outcome": None}
            return _FakeResult(("row",))
        if sql.startswith("UPDATE idempotency_keys"):
            key = (params["etype"], params["ikey"])
            if key in self._keys:
                self._keys[key]["outcome"] = "done"
            return _FakeResult(("row",))
        if sql.startswith("SELECT outcome FROM idempotency_keys"):
            key = (params["etype"], params["ikey"])
            if key in self._keys:
                return _FakeResult((self._keys[key]["outcome"],))
            return _FakeResult(None)
        return _FakeResult(None)


@pytest.mark.asyncio
async def test_dispatch_drops_legacy_output_chunk() -> None:
    """Legacy streaming envelopes are ack-and-dropped."""
    send_mock = AsyncMock()
    handled = await dispatch_discord_envelope(
        {
            "envelope_type": "discord.output_chunk.v1",
            "payload": json.dumps({"session_id": "s1", "thread_id": "t1"}),
        },
        db_factory=AsyncMock(),
        send_to_discord=send_mock,
        archive_discord_thread=AsyncMock(),
    )

    assert handled is True
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_discord_envelope_routes_system_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("discode.dispatcher.main._handle_system_notice", system_mock)

    handled = await dispatch_discord_envelope(
        {
            "envelope_type": "discord.system_notice.v1",
            "payload": json.dumps({"thread_id": "t1", "message": "hello"}),
        },
        db_factory=AsyncMock(),
        send_to_discord=AsyncMock(return_value="msg-2"),
        archive_discord_thread=AsyncMock(),
    )

    assert handled is True
    system_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_discord_envelope_routes_terminal_and_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal_mock = AsyncMock(return_value=True)
    archive_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("discode.dispatcher.main.handle_terminal_notice", terminal_mock)
    monkeypatch.setattr("discode.dispatcher.main.handle_archive_thread", archive_mock)

    terminal_handled = await dispatch_discord_envelope(
        {
            "envelope_type": "discord.terminal_notice.v1",
            "payload": json.dumps({"thread_id": "t1", "message": "done"}),
        },
        db_factory=AsyncMock(),
        send_to_discord=AsyncMock(return_value="msg-3"),
        archive_discord_thread=AsyncMock(),
    )
    archive_handled = await dispatch_discord_envelope(
        {
            "envelope_type": "discord.archive_thread.v1",
            "payload": json.dumps({"thread_id": "t1", "idempotency_key": "ikey-1"}),
        },
        db_factory=AsyncMock(),
        send_to_discord=AsyncMock(return_value="msg-4"),
        archive_discord_thread=AsyncMock(),
    )

    assert terminal_handled is True
    assert archive_handled is True
    terminal_mock.assert_awaited_once()
    archive_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_consumer_acknowledges_processed_messages() -> None:
    consumer = AsyncMock()
    consumer.claim_pending = AsyncMock(
        side_effect=[[("1-0", {"payload": "{}"})], asyncio.CancelledError],
    )
    consumer.ack = AsyncMock()

    async def handler(fields: dict[str, str]) -> bool:
        assert fields == {"payload": "{}"}
        return True

    with pytest.raises(asyncio.CancelledError):
        await _consume_dispatcher_stream(consumer, handler)

    consumer.ack.assert_awaited_once_with("1-0")


@pytest.mark.asyncio
async def test_run_dispatcher_uses_real_send_not_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_dispatcher must construct DiscordRestClient and use it for send_to_discord."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from discode.dispatcher import main as dispatcher_main

    send_calls: list[tuple[str, str, str | None]] = []

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
            send_calls.append((thread_id, content, nonce))
            return "fake-msg-id"

        async def archive_thread(self, *, thread_id: str) -> None:
            pass

    fake_client = FakeClient()
    fake_runtime = MagicMock()
    fake_runtime.settings.DISCORD_TOKEN = "Bot fake-token"
    # system_notice now goes through a DB idempotency gate (claim-then-finalize),
    # so provide a working in-memory fake db_factory rather than a bare MagicMock.
    fake_runtime.db_factory = _FakeIdemDB()
    fake_runtime.close = AsyncMock()

    # Dispatch one real envelope then cancel
    envelopes_to_dispatch = [
        (
            "1-0",
            {
                "envelope_type": "discord.system_notice.v1",
                "payload": (
                    '{"type":"discord.system_notice.v1","thread_id":"t1",'
                    '"message":"hello","idempotency_key":"k1"}'
                ),
            },
        ),
    ]
    consumer = AsyncMock()
    consumer.ensure_group = AsyncMock()
    consumer.claim_pending = AsyncMock(return_value=[])
    consumer.read_next = AsyncMock(side_effect=[envelopes_to_dispatch, asyncio.CancelledError])
    consumer.ack = AsyncMock()

    with (
        patch.object(dispatcher_main, "AppRuntime") as mock_runtime_cls,
        patch.object(
            dispatcher_main, "DiscordRestClient", return_value=fake_client
        ) as mock_client_cls,
        patch.object(dispatcher_main, "DispatcherConsumer", return_value=consumer),
    ):
        mock_runtime_cls.from_env.return_value = fake_runtime
        with pytest.raises((asyncio.CancelledError, Exception)):
            await dispatcher_main.run_dispatcher()

    # DiscordRestClient must have been constructed with the bot token
    mock_client_cls.assert_called_once_with(token="Bot fake-token")
    # The real client's send_message must have been called (not a stub)
    assert send_calls == [("t1", "hello", "k1")]
