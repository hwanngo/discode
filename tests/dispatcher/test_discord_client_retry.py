import json

import pytest

from discode.dispatcher.discord_client import DiscordRestClient


class _FakeResp:
    def __init__(self, status: int, body: dict | str = "") -> None:
        self.status = status
        self.ok = 200 <= status < 300
        self._body = body
        self.headers = (
            {"Retry-After": str(body.get("retry_after", "0"))} if isinstance(body, dict) else {}
        )

    async def __aenter__(self) -> _FakeResp:
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def text(self) -> str:
        return self._body if isinstance(self._body, str) else json.dumps(self._body)

    async def json(self) -> dict:
        return self._body if isinstance(self._body, dict) else {"id": "msg-1"}


class _FakeSession:
    def __init__(self, responses: list[_FakeResp]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict | None]] = []

    def post(self, url, json=None):  # noqa: A002
        self.calls.append((url, json))
        return self._responses.pop(0)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_429_then_success_retries_with_retry_after(monkeypatch):
    sess = _FakeSession(
        [
            _FakeResp(429, {"retry_after": 0.05, "global": False}),
            _FakeResp(200, {"id": "msg-42"}),
        ]
    )
    client = DiscordRestClient(token="abc")
    client._session = sess  # type: ignore[attr-defined]
    msg_id = await client.send_message(thread_id="t1", content="hi")
    assert msg_id == "msg-42"
    assert len(sess.calls) == 2


@pytest.mark.asyncio
async def test_5xx_retries_with_backoff_then_fails():
    sess = _FakeSession([_FakeResp(500, "boom"), _FakeResp(503, "boom"), _FakeResp(502, "boom")])
    client = DiscordRestClient(token="abc", max_retries=2)
    client._session = sess  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError) as ei:
        await client.send_message(thread_id="t1", content="hi")
    assert "502" in str(ei.value)
    assert len(sess.calls) == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_4xx_non_429_does_not_retry():
    sess = _FakeSession([_FakeResp(400, "bad")])
    client = DiscordRestClient(token="abc")
    client._session = sess  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError):
        await client.send_message(thread_id="t1", content="hi")
    assert len(sess.calls) == 1


@pytest.mark.asyncio
async def test_trigger_typing_posts_to_typing_endpoint():
    sess = _FakeSession([_FakeResp(204, "")])
    client = DiscordRestClient(token="abc")
    client._session = sess  # type: ignore[attr-defined]
    await client.trigger_typing(channel_id="ch1")
    url, body = sess.calls[0]
    assert url.endswith("/channels/ch1/typing")
    assert body is None or body == {}


@pytest.mark.asyncio
async def test_429_fires_retry_after_callback():
    """The set_retry_after_callback hook must fire on 429 with (thread_id, retry_after)."""
    calls: list[tuple[str, float]] = []
    sess = _FakeSession(
        [
            _FakeResp(429, {"retry_after": 0.01}),
            _FakeResp(200, {"id": "m"}),
        ]
    )
    client = DiscordRestClient(token="abc")
    client._session = sess  # type: ignore[attr-defined]
    client.set_retry_after_callback(lambda tid, secs: calls.append((tid, secs)))
    await client.send_message(thread_id="t1", content="hi")
    assert calls == [("t1", 0.01)]


@pytest.mark.asyncio
async def test_429_callback_exception_does_not_break_send():
    """A buggy callback must not propagate out of send_message."""
    sess = _FakeSession(
        [
            _FakeResp(429, {"retry_after": 0.01}),
            _FakeResp(200, {"id": "m"}),
        ]
    )
    client = DiscordRestClient(token="abc")
    client._session = sess  # type: ignore[attr-defined]

    def boom(tid: str, secs: float) -> None:
        raise RuntimeError("callback bug")

    client.set_retry_after_callback(boom)
    msg_id = await client.send_message(thread_id="t1", content="hi")
    assert msg_id == "m"
