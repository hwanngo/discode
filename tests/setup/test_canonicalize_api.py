from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from discode.runner.control_api import app


class _LoopbackTransport(ASGITransport):
    """ASGITransport that injects loopback client IP into the ASGI scope."""

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
async def loopback_client() -> AsyncGenerator[AsyncClient]:
    transport = _LoopbackTransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_canonicalize_safe_path_returns_allow(loopback_client: AsyncClient) -> None:
    response = await loopback_client.post("/v1/canonicalize_path", json={"path": "/tmp/safe_path"})
    assert response.status_code == 200
    data = response.json()
    assert data["denylist_verdict"] == "allow"
    assert data["denylist_reason"] is None


@pytest.mark.asyncio
async def test_canonicalize_ssh_path_returns_deny(loopback_client: AsyncClient) -> None:
    response = await loopback_client.post(
        "/v1/canonicalize_path", json={"path": "/home/user/.ssh/id_rsa"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["denylist_verdict"] == "deny"
    assert data["denylist_reason"] is not None
    assert ".ssh" in data["denylist_reason"]


@pytest.mark.asyncio
async def test_canonicalize_returns_required_fields(loopback_client: AsyncClient) -> None:
    response = await loopback_client.post("/v1/canonicalize_path", json={"path": "/tmp"})
    assert response.status_code == 200
    data = response.json()
    assert "realpath" in data
    assert "exists" in data
    assert "is_dir" in data
    assert "denylist_verdict" in data
    assert "denylist_reason" in data
    assert isinstance(data["realpath"], str)
    assert isinstance(data["exists"], bool)
    assert isinstance(data["is_dir"], bool)
