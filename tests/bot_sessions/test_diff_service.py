from __future__ import annotations

import pytest
from httpx import ConnectError, Request, Response

from discode.bot import diff_service


class _OkClient:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def __aenter__(self) -> _OkClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    async def post(self, url: str, json: dict[str, str], **kwargs: object) -> Response:
        del url, json, kwargs
        return Response(200, json={"patch": "diff --git a/a b/a\n", "truncated": False})


class _ConnectFailClient:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def __aenter__(self) -> _ConnectFailClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    async def post(self, url: str, json: dict[str, str], **kwargs: object) -> Response:
        del json, kwargs
        raise ConnectError("connection refused", request=Request("POST", url))


class _Non200Client:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def __aenter__(self) -> _Non200Client:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    async def post(self, url: str, json: dict[str, str], **kwargs: object) -> Response:
        del url, json, kwargs
        return Response(400, json={"detail": "cwd_must_be_directory"})


@pytest.mark.asyncio
async def test_fetch_diff_preview_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_CONTROL_BIND", "127.0.0.1:8788")
    monkeypatch.setattr(diff_service.httpx, "AsyncClient", _OkClient)

    result = await diff_service.fetch_diff_preview("/tmp/repo")

    assert result.patch.startswith("diff --git")
    assert result.truncated is False


@pytest.mark.asyncio
async def test_fetch_diff_preview_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diff_service.httpx, "AsyncClient", _ConnectFailClient)

    with pytest.raises(ValueError, match="Failed to fetch diff preview:"):
        await diff_service.fetch_diff_preview("/tmp/repo")


@pytest.mark.asyncio
async def test_fetch_diff_preview_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diff_service.httpx, "AsyncClient", _Non200Client)

    with pytest.raises(ValueError, match="cwd_must_be_directory"):
        await diff_service.fetch_diff_preview("/tmp/repo")


class _WrongTypesClient:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def __aenter__(self) -> _WrongTypesClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    async def post(self, url: str, json: dict[str, str], **kwargs: object) -> Response:
        del url, json, kwargs
        return Response(200, json={"patch": 123, "truncated": "yes"})


class _NonJsonClient:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def __aenter__(self) -> _NonJsonClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    async def post(self, url: str, json: dict[str, str], **kwargs: object) -> Response:
        del url, json, kwargs
        return Response(200, content=b"not json", headers={"content-type": "text/plain"})


@pytest.mark.asyncio
async def test_fetch_diff_preview_wrong_payload_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diff_service.httpx, "AsyncClient", _WrongTypesClient)

    with pytest.raises(ValueError, match="invalid response payload"):
        await diff_service.fetch_diff_preview("/tmp/repo")


@pytest.mark.asyncio
async def test_fetch_diff_preview_non_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diff_service.httpx, "AsyncClient", _NonJsonClient)

    with pytest.raises(ValueError, match="invalid response payload"):
        await diff_service.fetch_diff_preview("/tmp/repo")
