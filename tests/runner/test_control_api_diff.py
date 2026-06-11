from __future__ import annotations

import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from discode.runner.control_api import app


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
async def loopback_client() -> AsyncGenerator[AsyncClient]:
    transport = _LoopbackTransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def _make_git_repo_with_change(tmp_path: Path, *, content: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n")
    subprocess.run(
        ["git", "add", "tracked.txt"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "base"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked.write_text(content)
    return repo


@pytest.mark.asyncio
async def test_diff_preview_git_repo_returns_patch(
    loopback_client: AsyncClient,
    tmp_path: Path,
) -> None:
    repo = _make_git_repo_with_change(tmp_path, content="base\nchanged\n")
    response = await loopback_client.post("/v1/diff_preview", json={"cwd": str(repo)})
    assert response.status_code == 200
    data = response.json()
    assert "diff --git" in data["patch"]
    assert data["truncated"] is False


@pytest.mark.asyncio
async def test_diff_preview_non_git_returns_empty_patch(
    loopback_client: AsyncClient, tmp_path: Path
) -> None:
    non_git_dir = tmp_path / "plain-dir"
    non_git_dir.mkdir()
    response = await loopback_client.post("/v1/diff_preview", json={"cwd": str(non_git_dir)})
    assert response.status_code == 200
    assert response.json() == {"patch": "", "truncated": False}


@pytest.mark.asyncio
async def test_diff_preview_large_diff_truncates(
    loopback_client: AsyncClient,
    tmp_path: Path,
) -> None:
    content = "base\n" + ("x" * 80 + "\n") * 1800
    repo = _make_git_repo_with_change(tmp_path, content=content)
    response = await loopback_client.post("/v1/diff_preview", json={"cwd": str(repo)})
    assert response.status_code == 200
    data = response.json()
    assert data["truncated"] is True
    assert len(data["patch"].encode("utf-8")) <= 65536
