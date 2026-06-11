from __future__ import annotations

import importlib
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient


class _LoopbackTransport(ASGITransport):
    async def handle_async_request(self, request: Any) -> Any:
        original_app = self.app

        async def patched_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            if scope.get("type") == "http":
                scope["client"] = ("127.0.0.1", 12345)
            await original_app(scope, receive, send)

        self.app = patched_app
        try:
            return await super().handle_async_request(request)
        finally:
            self.app = original_app


@pytest.fixture
async def app_with_auth(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[Any]:
    monkeypatch.setenv("RUNNER_CONTROL_AUTH", "test-bearer")
    monkeypatch.setenv("RUNNER_CONTROL_BIND", "127.0.0.1:8788")

    import discode.runner.control_api as mod

    importlib.reload(mod)

    discord_mock = AsyncMock()
    discord_mock.send_message.return_value = "msg-123"

    rate_mock = AsyncMock()

    session_lookup = AsyncMock()
    session_lookup.return_value = {"thread_id": "thr-1", "status": "running"}

    mod.app.state.discord_client = discord_mock
    mod.app.state.rate_limiter = rate_mock
    mod.app.state.session_lookup = session_lookup

    try:
        yield mod.app, discord_mock, rate_mock, session_lookup
    finally:
        # Reload again with cleared env so other tests don't pick up auth
        monkeypatch.delenv("RUNNER_CONTROL_AUTH", raising=False)
        importlib.reload(mod)


def _client(app: Any) -> AsyncClient:
    transport = _LoopbackTransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_reply_happy_path(app_with_auth: Any) -> None:
    app, discord_mock, rate_mock, _ = app_with_auth
    async with _client(app) as c:
        resp = await c.post(
            "/v1/sessions/sid-abc/reply",
            json={"text": "hello", "idempotency_key": "ikey-1"},
            headers={"Authorization": "Bearer test-bearer"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"message_id": "msg-123", "message_ids": ["msg-123"], "delivered": True}
    rate_mock.acquire.assert_awaited_once_with("thr-1")
    discord_mock.send_message.assert_awaited_once_with(
        thread_id="thr-1", content="hello", nonce="ikey-1"
    )


@pytest.mark.asyncio
async def test_reply_redacts_secrets_before_send(app_with_auth: Any) -> None:
    """FIX 1: the reply body text must be redacted before reaching Discord."""
    app, discord_mock, _, _ = app_with_auth
    secret_url = "postgresql://admin:hunter2@db.internal:5432/prod"
    token = "MTk4NzY1NDMyMTA5ODc2NTQz.GhI_jk.aBcDeFgHiJkLmNoPqRsTuVwXyZ012345678"
    text = f"db {secret_url} token {token}"
    async with _client(app) as c:
        resp = await c.post(
            "/v1/sessions/sid-abc/reply",
            json={"text": text, "idempotency_key": "ikey-1"},
            headers={"Authorization": "Bearer test-bearer"},
        )
    assert resp.status_code == 200
    sent = discord_mock.send_message.await_args.kwargs["content"]
    assert "hunter2" not in sent
    assert token not in sent
    assert "[REDACTED]" in sent


@pytest.mark.asyncio
async def test_reply_unauthorized(app_with_auth: Any) -> None:
    app, *_ = app_with_auth
    async with _client(app) as c:
        resp = await c.post(
            "/v1/sessions/sid-abc/reply",
            json={"text": "x", "idempotency_key": "k"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_reply_session_not_found(app_with_auth: Any) -> None:
    app, _, _, session_lookup = app_with_auth
    session_lookup.return_value = None
    async with _client(app) as c:
        resp = await c.post(
            "/v1/sessions/missing/reply",
            json={"text": "x", "idempotency_key": "k"},
            headers={"Authorization": "Bearer test-bearer"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reply_session_dead(app_with_auth: Any) -> None:
    app, _, _, session_lookup = app_with_auth
    session_lookup.return_value = {"thread_id": "thr-1", "status": "stopped"}
    async with _client(app) as c:
        resp = await c.post(
            "/v1/sessions/dead/reply",
            json={"text": "x", "idempotency_key": "k"},
            headers={"Authorization": "Bearer test-bearer"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reply_503_when_state_unwired(monkeypatch: pytest.MonkeyPatch) -> None:
    """If app.state lacks the collaborators, return 503 (runner not fully started)."""
    monkeypatch.setenv("RUNNER_CONTROL_AUTH", "test-bearer")
    monkeypatch.setenv("RUNNER_CONTROL_BIND", "127.0.0.1:8788")
    import discode.runner.control_api as mod

    importlib.reload(mod)
    # Deliberately don't attach state.
    async with _client(mod.app) as c:
        resp = await c.post(
            "/v1/sessions/sid/reply",
            json={"text": "x", "idempotency_key": "k"},
            headers={"Authorization": "Bearer test-bearer"},
        )
    assert resp.status_code == 503
    monkeypatch.delenv("RUNNER_CONTROL_AUTH", raising=False)
    importlib.reload(mod)
