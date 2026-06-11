"""Shared test doubles for the dispatcher's aiohttp-backed Discord client.

aioresponses (0.7.8, latest) is incompatible with aiohttp >= 3.14 — its
``build_response`` constructs ``ClientResponse`` without the now-required
``stream_writer`` keyword. Rather than pin aiohttp below the security-patched
3.14, we mock at the session seam with a tiny fake that mirrors the slice of the
aiohttp API ``DiscordRestClient`` actually uses (``post``/``patch`` returning an
async-context-manager response with ``ok``/``status``/``json``/``text``/
``headers``).
"""

from __future__ import annotations

import json as _json
from typing import Any


class FakeResponse:
    """Async-context-manager stand-in for ``aiohttp.ClientResponse``."""

    def __init__(
        self,
        status: int = 200,
        payload: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._payload = {} if payload is None else payload
        self.headers = headers or {}

    @property
    def ok(self) -> bool:
        # Matches aiohttp.ClientResponse.ok (True when status < 400).
        return self.status < 400

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def json(self) -> Any:
        return self._payload

    async def text(self) -> str:
        return _json.dumps(self._payload)


class FakeSession:
    """Records requests and returns configured responses; no network."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._routes: dict[tuple[str, str], FakeResponse] = {}
        self.default = FakeResponse(200, {"id": "0"})

    def set_response(
        self,
        method: str,
        url: str,
        *,
        status: int = 200,
        payload: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._routes[(method.upper(), url)] = FakeResponse(status, payload, headers)

    def _respond(self, method: str, url: str, json: Any = None) -> FakeResponse:
        self.calls.append({"method": method, "url": url, "json": json})
        return self._routes.get((method, url), self.default)

    def post(self, url: str, *, json: Any = None, **_: Any) -> FakeResponse:
        return self._respond("POST", url, json=json)

    def patch(self, url: str, *, json: Any = None, **_: Any) -> FakeResponse:
        return self._respond("PATCH", url, json=json)

    async def close(self) -> None:
        return None

    # Convenience for assertions ------------------------------------------------
    def last_json(self) -> dict[str, Any]:
        return self.calls[-1]["json"] or {}

    def json_for(self, method: str, url: str) -> dict[str, Any]:
        for call in self.calls:
            if call["method"] == method.upper() and call["url"] == url:
                return call["json"] or {}
        raise AssertionError(f"no {method} {url} call recorded; calls={self.calls}")
