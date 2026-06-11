from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from discode.runner.control_api import app


class _FakeClientTransport(ASGITransport):
    """ASGITransport subclass that injects a custom client IP into the ASGI scope."""

    def __init__(self, fake_client_host: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._fake_client_host = fake_client_host

    async def handle_async_request(self, request: Any) -> Any:
        # Monkey-patch the app call to inject a fake client address
        original_app = self.app

        async def patched_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            if scope.get("type") == "http":
                scope["client"] = (self._fake_client_host, 12345)
            await original_app(scope, receive, send)

        self.app = patched_app
        try:
            return await super().handle_async_request(request)
        finally:
            self.app = original_app


@pytest.fixture
async def loopback_client() -> AsyncGenerator[AsyncClient]:
    transport = _FakeClientTransport(fake_client_host="127.0.0.1", app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
async def non_loopback_client() -> AsyncGenerator[AsyncClient]:
    transport = _FakeClientTransport(fake_client_host="203.0.113.1", app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


class TestPreflightEndpoint:
    async def test_loopback_returns_200(self, loopback_client: AsyncClient) -> None:
        response = await loopback_client.get("/v1/preflight")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["preflight_passed"] is True
        assert "isolation_mode" in data

    async def test_non_loopback_returns_403(self, non_loopback_client: AsyncClient) -> None:
        response = await non_loopback_client.get("/v1/preflight")
        assert response.status_code == 403
