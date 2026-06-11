from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class DiffPreviewResult:
    patch: str
    truncated: bool


def _runner_control_base_url() -> str:
    bind = os.environ.get("RUNNER_CONTROL_BIND", "127.0.0.1:8788").strip()
    if bind.startswith("http://") or bind.startswith("https://"):
        return bind.rstrip("/")
    return f"http://{bind}"


def _runner_control_auth_headers() -> dict[str, str]:
    auth = os.environ.get("RUNNER_CONTROL_AUTH", "")
    if auth:
        return {"Authorization": f"Bearer {auth}"}
    return {}


async def fetch_diff_preview(cwd: str) -> DiffPreviewResult:
    url = f"{_runner_control_base_url()}/v1/diff_preview"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                url, json={"cwd": cwd}, headers=_runner_control_auth_headers()
            )
    except httpx.HTTPError as exc:
        raise ValueError(f"Failed to fetch diff preview: {exc}") from exc

    if response.status_code != 200:
        detail: str | None = None
        try:
            body = response.json()
            if isinstance(body, dict):
                raw_detail = body.get("detail")
                if raw_detail is not None:
                    detail = str(raw_detail)
        except ValueError:
            detail = None
        message = detail or response.text or f"HTTP {response.status_code}"
        raise ValueError(f"Failed to fetch diff preview: {message}")

    try:
        payload: Any = response.json()
    except (ValueError, Exception) as exc:
        raise ValueError("Failed to fetch diff preview: invalid response payload") from exc
    patch = payload.get("patch")
    truncated = payload.get("truncated")
    if not isinstance(patch, str) or not isinstance(truncated, bool):
        raise ValueError("Failed to fetch diff preview: invalid response payload")
    return DiffPreviewResult(patch=patch, truncated=truncated)
