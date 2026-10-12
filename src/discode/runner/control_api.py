from __future__ import annotations

import ipaddress
import logging
import os
import secrets
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware

from discode.config import settings
from discode.dispatcher.message_builder import build_message
from discode.security.paths import canonicalize
from discode.security.redaction import apply_common_redaction

logger = logging.getLogger(__name__)

_LOOPBACK_IPS = {"127.0.0.1", "::1"}
_DIFF_PREVIEW_MAX_BYTES = 64 * 1024


class LoopbackOnlyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        client = request.client
        if client is None or (
            _is_loopback_only(settings.RUNNER_CONTROL_BIND) and client.host not in _LOOPBACK_IPS
        ):
            return JSONResponse({"detail": "forbidden: non-loopback client"}, status_code=403)
        return await call_next(request)  # type: ignore[no-any-return]


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Validate Authorization: Bearer <token> when a token is configured."""

    def __init__(self, app: Any, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        header = request.headers.get("Authorization", "")
        token = self._token or settings.RUNNER_CONTROL_AUTH
        expected = f"Bearer {token}"
        if not token or not secrets.compare_digest(header.encode(), expected.encode()):
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
    host = bind.rsplit(":", 1)[0].strip("[]")
    return host in _LOOPBACK_IPS or host == "localhost"


async def _startup_validation() -> None:
    bind = settings.RUNNER_CONTROL_BIND
    host = bind.rsplit(":", 1)[0].strip("[]")
    if not settings.RUNNER_CONTROL_AUTH:
        raise RuntimeError("control_api_auth_required")
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
    _app.add_middleware(BearerAuthMiddleware, token="")
    _app.add_middleware(LoopbackOnlyMiddleware)  # outermost: added last, runs first
    return _app


app = _build_app()


@app.get("/v1/preflight")
async def preflight() -> dict[str, Any]:
    from discode.security.execution_policy import ensure_execution_allowed

    try:
        ensure_execution_allowed()
    except ValueError as exc:
        return {"ok": False, "preflight_passed": False, "reason": str(exc)}
    return {
        "ok": True,
        "isolation_mode": settings.SESSION_ISOLATION_MODE,
        "preflight_passed": True,
        "trusted_host": True,
        "sandboxed": False,
    }


class PathBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096, pattern=r"^[^\x00]+$")


@app.post("/v1/canonicalize_path")
async def canonicalize_path(body: PathBody) -> dict[str, Any]:
    realpath, deny_reason = canonicalize(body.path)
    exists = os.path.exists(realpath)
    return {
        "realpath": realpath,
        "exists": exists,
        "is_dir": os.path.isdir(realpath),
        "denylist_verdict": "deny" if deny_reason else "allow",
        "denylist_reason": deny_reason,
    }


async def _neutralized_filter_args(realpath: str, env: dict[str, str]) -> list[str]:
    """``-c`` overrides disabling every configured git filter driver.

    ``git diff`` runs ``filter.<name>.clean``/``process`` commands that a workspace
    can define in ``.git/config`` plus ``.gitattributes``/``.git/info/attributes``;
    ``--no-ext-diff``/``--no-textconv`` do not stop them. Reading config runs nothing.
    """
    from discode.tools.exec_session import ExecFailed, ExecSession

    listing = ExecSession(
        argv=["git", "-C", realpath, "config", "--name-only", "--get-regexp", r"^filter\."],
        cwd=realpath,
        env=env,
        use_pty=False,
        max_output_bytes=_DIFF_PREVIEW_MAX_BYTES,
    )
    try:
        keys = (await listing.run(timeout=10)).text.splitlines()
    except ExecFailed as exc:
        if exc.exit_code == 1:  # no filter.* keys configured
            return []
        raise
    names = {key.strip()[len("filter.") :].rsplit(".", 1)[0] for key in keys if key.strip()}
    if any("=" in name or not name for name in names):
        raise HTTPException(status_code=422, detail="diff_failed: unsupported filter config")
    args: list[str] = []
    for name in sorted(names):
        for var, value in (("clean", ""), ("smudge", ""), ("process", ""), ("required", "false")):
            args += ["-c", f"filter.{name}.{var}={value}"]
    return args


class DiffBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    guild_id: str = Field(min_length=1, max_length=32)


@app.post("/v1/diff_preview")
async def diff_preview(body: DiffBody, request: Request) -> dict[str, Any]:
    lookup = getattr(request.app.state, "session_lookup", None)
    info = await lookup(body.session_id) if lookup else None
    if info is None or str(info.get("guild_id")) != body.guild_id:
        raise HTTPException(status_code=404, detail="session_not_found")
    realpath, deny_reason = canonicalize(info.get("cwd_realpath") or "")
    if deny_reason or not os.path.isdir(realpath):
        raise HTTPException(status_code=400, detail="cwd_unavailable")
    from discode.security.env_scrub import scrub_env
    from discode.tools.exec_session import ExecFailed, ExecSession, ExecTimeout

    env = scrub_env(dict(os.environ))
    try:
        filter_args = await _neutralized_filter_args(realpath, env)
        execution = ExecSession(
            argv=[
                "git",
                "--no-pager",
                "-c",
                "core.fsmonitor=false",
                *filter_args,
                "-C",
                realpath,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                # Submodules carry their own config (and filters).
                "--ignore-submodules=all",
                "--no-color",
                "--patch",
            ],
            cwd=realpath,
            env=env,
            use_pty=False,
            max_output_bytes=_DIFF_PREVIEW_MAX_BYTES,
        )
        result = await execution.run(timeout=10)
    except ExecTimeout as exc:
        raise HTTPException(status_code=504, detail="diff_timeout") from exc
    except ExecFailed as exc:
        raise HTTPException(
            status_code=422, detail="diff_failed: workspace is not a readable Git repository"
        ) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="git_unavailable") from exc
    data = result.text.encode("utf-8")
    return {
        "patch": data[:_DIFF_PREVIEW_MAX_BYTES].decode("utf-8", errors="ignore"),
        "truncated": len(data) > _DIFF_PREVIEW_MAX_BYTES,
    }


class ReplyBody(BaseModel):
    text: str = Field(max_length=1_000_000)
    idempotency_key: str = Field(min_length=1, max_length=200)
    lifecycle_version: int | None = Field(default=None, ge=0)


async def _send_checkpointed(
    db_factory: async_sessionmaker[AsyncSession] | None,
    sid: str,
    key: str,
    send: Callable[[], Awaitable[str]],
) -> str:
    if db_factory is None:
        return await send()
    owner = str(uuid.uuid4())
    # A bounded lease avoids holding database connections across Discord I/O.
    # Pending delivery retries use the same nonce; successful checkpoints skip it.
    async with db_factory() as db, db.begin():
        await db.execute(
            text("""
            INSERT INTO idempotency_keys
                (envelope_type,idempotency_key,session_id,expires_at,outcome)
            VALUES ('discord.reply_chunk.v1',:key,:sid,'infinity'::timestamptz,'pending')
            ON CONFLICT DO NOTHING
        """),
            {"key": key, "sid": sid},
        )
        outcome = await db.scalar(
            text("""
            SELECT outcome FROM idempotency_keys
            WHERE envelope_type='discord.reply_chunk.v1' AND idempotency_key=:key
        """),
            {"key": key},
        )
        if outcome and outcome != "pending":
            return str(outcome)
        claimed = await db.scalar(
            text("""
            UPDATE idempotency_keys SET claim_owner=:owner,
                claim_expires_at=now()+interval '10 minutes'
            WHERE envelope_type='discord.reply_chunk.v1' AND idempotency_key=:key
                AND outcome='pending'
                AND (claim_expires_at IS NULL OR claim_expires_at <= now())
            RETURNING idempotency_key
        """),
            {"key": key, "owner": owner},
        )
    if claimed is None:
        raise HTTPException(status_code=409, detail="reply_chunk_in_progress")
    try:
        message_id = await send()
        async with db_factory() as db, db.begin():
            await db.execute(
                text("""
                UPDATE idempotency_keys SET outcome=:message_id,claim_expires_at=NULL
                WHERE envelope_type='discord.reply_chunk.v1' AND idempotency_key=:key
                    AND claim_owner=:owner
            """),
                {"key": key, "message_id": message_id, "owner": owner},
            )
        return message_id
    finally:
        async with db_factory() as db, db.begin():
            await db.execute(
                text("""
                UPDATE idempotency_keys SET claim_expires_at=NULL
                WHERE envelope_type='discord.reply_chunk.v1' AND idempotency_key=:key
                    AND claim_owner=:owner
            """),
                {"key": key, "owner": owner},
            )


@app.post("/v1/sessions/{sid}/reply")
async def post_reply(sid: str, body: ReplyBody, request: Request) -> dict[str, Any]:
    state = request.app.state
    lookup = getattr(state, "session_lookup", None)
    discord_client = getattr(state, "discord_client", None)
    rate_limiter = getattr(state, "rate_limiter", None)
    if lookup is None or discord_client is None or rate_limiter is None:
        raise HTTPException(status_code=503, detail="reply pipeline not wired")

    info = await lookup(sid)
    if info is None or info.get("status") not in ("running", "idle"):
        raise HTTPException(status_code=404, detail="session_not_found_or_dead")

    if (
        body.lifecycle_version is not None
        and info.get("lifecycle_version") != body.lifecycle_version
    ):
        raise HTTPException(status_code=409, detail="session_version_changed")
    from discode.dispatcher.discord_client import DiscordPermanentError
    from discode.security.execution_policy import ExecutionDenied, ensure_outbound_allowed

    try:
        ensure_outbound_allowed()
    except ExecutionDenied as exc:
        # EMERGENCY_STOP: transient from the caller's point of view; retry later.
        raise HTTPException(status_code=503, detail="outbound_disabled") from exc
    thread_id = info["thread_id"]
    # Redact secrets in the reply text before it is chunked and sent to Discord.
    redacted_text = apply_common_redaction(body.text)
    chunks = build_message(redacted_text) or [""]
    message_ids: list[str] = []
    for idx, chunk in enumerate(chunks):
        nonce = body.idempotency_key if idx == 0 else f"{body.idempotency_key}:{idx}"

        async def send_chunk(content: str = chunk, chunk_nonce: str = nonce) -> str:
            await rate_limiter.acquire(thread_id)
            current = await lookup(sid)
            if (
                current is None
                or current.get("status") not in ("running", "idle")
                or current.get("lifecycle_version") != info.get("lifecycle_version")
            ):
                raise HTTPException(status_code=409, detail="session_version_changed")
            ensure_outbound_allowed()
            quota = getattr(state, "output_quota", None)
            if quota is not None and not await quota(
                sid, chunk_nonce, len(content.encode("utf-8"))
            ):
                raise HTTPException(
                    status_code=429, detail="output_quota", headers={"Retry-After": "60"}
                )
            return str(
                await discord_client.send_message(
                    thread_id=thread_id, content=content, nonce=chunk_nonce
                )
            )

        try:
            message_ids.append(
                await _send_checkpointed(
                    getattr(state, "db_factory", None), sid, f"{sid}:{nonce}", send_chunk
                )
            )
        except DiscordPermanentError as exc:
            # 410 tells the caller this reply can never be delivered.
            raise HTTPException(status_code=410, detail=f"discord_rejected:{exc.status}") from exc
    return {"message_id": message_ids[0], "message_ids": message_ids, "delivered": True}
