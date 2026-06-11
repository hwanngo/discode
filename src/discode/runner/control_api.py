from __future__ import annotations

import ipaddress
import logging
import os
import secrets
import subprocess
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from discode.dispatcher.message_builder import build_message
from discode.security.paths import canonicalize
from discode.security.redaction import apply_common_redaction

logger = logging.getLogger(__name__)

_LOOPBACK_IPS = {"127.0.0.1", "::1"}
_DIFF_PREVIEW_MAX_BYTES = 64 * 1024


class LoopbackOnlyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        client = request.client
        if client is None or client.host not in _LOOPBACK_IPS:
            return JSONResponse({"detail": "forbidden: non-loopback client"}, status_code=403)
        return await call_next(request)  # type: ignore[no-any-return]


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Validate Authorization: Bearer <token> when a token is configured."""

    def __init__(self, app: Any, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        header = request.headers.get("Authorization", "")
        expected = f"Bearer {self._token}"
        if not secrets.compare_digest(header.encode(), expected.encode()):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)  # type: ignore[no-any-return]


def _is_private_or_loopback(host: str) -> bool:
    """Return True if *host* is a loopback, RFC1918 private, or link-local address."""
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_loopback or addr.is_private or addr.is_link_local
    except ValueError:
        # hostname fallback: treat 'localhost' as loopback
        return host == "localhost"


def _is_loopback_only(bind: str) -> bool:
    host = bind.split(":")[0]
    return _is_private_or_loopback(host)


async def _startup_validation() -> None:
    bind = os.environ.get("RUNNER_CONTROL_BIND", "127.0.0.1:8788")
    host = bind.split(":")[0]
    # Reject public (non-loopback, non-private) binds.
    # 0.0.0.0 is rejected unconditionally (wildcard bind exposes all interfaces).
    if host == "0.0.0.0" or not _is_private_or_loopback(host):  # noqa: S104
        logger.error("RUNNER_CONTROL_BIND resolves to a public IP (%s) — refusing to start", host)
        raise RuntimeError("control_api_public_bind_refused")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    await _startup_validation()
    yield


def _build_app() -> FastAPI:
    _app = FastAPI(title="discode-runner-control", lifespan=_lifespan)
    # add_middleware is LIFO: add BearerAuth first (inner) so LoopbackOnly
    # (added last) becomes the outermost layer and executes first on every request.
    control_auth = os.environ.get("RUNNER_CONTROL_AUTH", "")
    if control_auth:
        _app.add_middleware(BearerAuthMiddleware, token=control_auth)
    elif not _is_loopback_only(os.environ.get("RUNNER_CONTROL_BIND", "127.0.0.1:8788")):
        logger.warning(
            "RUNNER_CONTROL_AUTH is not set but RUNNER_CONTROL_BIND is non-loopback. "
            "Control API is unauthenticated on the network interface."
        )
    _app.add_middleware(LoopbackOnlyMiddleware)  # outermost: added last, runs first
    return _app


app = _build_app()


@app.get("/v1/preflight")
async def preflight() -> dict[str, Any]:
    isolation_mode = os.environ.get("SESSION_ISOLATION_MODE", "platform_equivalent")
    return {"ok": True, "isolation_mode": isolation_mode, "preflight_passed": True}


@app.post("/v1/canonicalize_path")
async def canonicalize_path(request: Request) -> dict[str, Any]:
    body = await request.json()
    path = body.get("path", "")
    realpath, deny_reason = canonicalize(path)
    exists = os.path.exists(realpath)
    is_dir = os.path.isdir(realpath) if exists else False
    return {
        "realpath": realpath,
        "exists": exists,
        "is_dir": is_dir,
        "denylist_verdict": "deny" if deny_reason else "allow",
        "denylist_reason": deny_reason,
    }


@app.post("/v1/diff_preview")
async def diff_preview(request: Request) -> dict[str, Any]:
    body = await request.json()
    cwd = body.get("cwd", "")
    realpath, deny_reason = canonicalize(cwd)
    if deny_reason:
        raise HTTPException(status_code=400, detail=deny_reason)
    if not os.path.isdir(realpath):
        raise HTTPException(status_code=400, detail="cwd_must_be_directory")

    proc = subprocess.run(
        ["git", "-C", realpath, "diff", "--no-color", "--patch"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").lower()
        if "not a git repository" in stderr:
            return {"patch": "", "truncated": False}
        logger.warning(
            "diff_preview failed for %s (rc=%s): %s",
            realpath,
            proc.returncode,
            proc.stderr,
        )
        return {"patch": "", "truncated": False}

    patch_bytes = proc.stdout.encode("utf-8")
    truncated = len(patch_bytes) > _DIFF_PREVIEW_MAX_BYTES
    if truncated:
        patch = patch_bytes[:_DIFF_PREVIEW_MAX_BYTES].decode("utf-8", errors="ignore")
        return {"patch": patch, "truncated": True}
    return {"patch": proc.stdout, "truncated": False}


class ReplyBody(BaseModel):
    text: str
    idempotency_key: str


@app.post("/v1/sessions/{sid}/reply")
async def post_reply(sid: str, body: ReplyBody, request: Request) -> dict[str, Any]:
    state = request.app.state
    lookup = getattr(state, "session_lookup", None)
    discord_client = getattr(state, "discord_client", None)
    rate_limiter = getattr(state, "rate_limiter", None)
    if lookup is None or discord_client is None or rate_limiter is None:
        raise HTTPException(status_code=503, detail="reply pipeline not wired")

    info = await lookup(sid)
    if info is None or info.get("status") in (None, "failed", "orphaned", "stopped"):
        raise HTTPException(status_code=404, detail="session_not_found_or_dead")

    thread_id = info["thread_id"]
    # Redact secrets in the reply text before it is chunked and sent to Discord.
    redacted_text = apply_common_redaction(body.text)
    chunks = build_message(redacted_text) or [""]
    message_ids: list[str] = []
    for idx, chunk in enumerate(chunks):
        await rate_limiter.acquire(thread_id)
        nonce = body.idempotency_key if idx == 0 else f"{body.idempotency_key}:{idx}"
        message_ids.append(
            await discord_client.send_message(thread_id=thread_id, content=chunk, nonce=nonce)
        )
    return {"message_id": message_ids[0], "message_ids": message_ids, "delivered": True}
