from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.runner.reply_client import ReplyError
from discode.security.env_scrub import scrub_env
from discode.tools.errors import BadSessionConfig
from discode.tools.generic import AmbiguousPrompt
from discode.tools.registry import get_adapter
from discode.tools.text import strip_ansi as _strip_ansi

if TYPE_CHECKING:
    from discode.runner.reply_client import ReplyClient

logger = logging.getLogger(__name__)

_EXEC_TIMEOUT_SECONDS = 300.0
# Below the control API's ReplyBody.text max_length (1,000,000) with room for a notice.
_MAX_REPLY_CHARS = 900_000
# How often an executing turn re-checks that its session is still live.
_REVOCATION_POLL_SECONDS = 2.0


_DEFAULT_EXTRA_PATHS: tuple[str, ...] = (
    # macOS — Homebrew
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    # Per-user installs (npm-global, pipx, cargo, bun, pnpm, nix, asdf shims)
    "~/.local/bin",
    "~/.npm-global/bin",
    "~/.cargo/bin",
    "~/.bun/bin",
    "~/.local/share/pnpm",
    "~/.nix-profile/bin",
    "~/.asdf/shims",
    # System-wide
    "/usr/local/bin",
    "/usr/local/sbin",
    # Linux distro extras
    "/snap/bin",
    "/var/lib/flatpak/exports/bin",
    # Standard system bins (usually already in PATH but harmless to ensure)
    "/usr/bin",
    "/bin",
)


def _normalize_extra_paths(raw: list[str]) -> list[str]:
    """Expand ~, drop dirs that don't exist on disk, dedup preserving order.

    Used by build_exec_session to turn a list of candidate bin directories
    into a clean PATH prefix. Adapters and the default allowlist may include
    `~`-prefixed paths; expansion happens here so call sites stay simple.
    """
    import os as _os

    expanded = [_os.path.expanduser(p) for p in raw]
    existing = [p for p in expanded if _os.path.isdir(p)]
    seen: set[str] = set()
    unique = []
    for path in existing:
        if path not in seen:
            unique.append(path)
            seen.add(path)
    return unique


def is_base_url_allowed(url: str) -> bool:
    """Validate a per-session base_url override against SSRF (FIX 2).

    Rejects:
    - schemes other than http/https
    - URLs with no resolvable host
    - hosts that resolve to private / loopback / link-local / reserved /
      unspecified / multicast addresses (incl. 169.254.169.254 metadata).

    All resolved addresses must be public for the URL to be allowed.
    """
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except Exception:
        return False

    if parts.scheme not in ("http", "https"):
        return False
    host = parts.hostname
    if not host:
        return False

    # Collect candidate IPs: either the literal host or all DNS resolutions.
    candidates: list[str] = []
    try:
        ipaddress.ip_address(host)
        candidates.append(host)
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except Exception:
            return False
        candidates = [str(info[4][0]) for info in infos]
        if not candidates:
            return False

    for cand in candidates:
        try:
            ip = ipaddress.ip_address(cand)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
            or ip.is_multicast
        ):
            return False
    return True


async def _load_overrides(session: AsyncSession, store_entry: dict[str, Any]) -> dict[str, str]:
    """Load per-session config overrides from the DB. Test seam (patch this)."""
    from sqlalchemy import select as _select

    from discode.bot.session_config_service import decrypt_value_ciphertext
    from discode.db.models import SessionConfig

    sid = store_entry.get("sid")
    overrides: dict[str, str] = {}
    if sid is None:
        return overrides
    override_rows = (
        await session.scalars(_select(SessionConfig).where(SessionConfig.session_id == sid))
    ).all()
    for row in override_rows:
        if row.value is not None:
            overrides[row.key] = row.value
        elif row.value_ciphertext is not None:
            try:
                overrides[row.key] = decrypt_value_ciphertext(row.value_ciphertext)
            except Exception as exc:
                raise BadSessionConfig(str(sid), f"{row.key} decryption failed") from exc
    return overrides


async def build_exec_session(
    store_entry: dict[str, Any], payload: InputJobPayload, session: AsyncSession
) -> Any:
    """Construct an ExecSession for one turn. Test seam (patch this)."""
    import os as _os
    import shutil as _shutil

    from discode.security.execution_policy import ensure_execution_allowed
    from discode.tools.exec_session import ExecSession

    ensure_execution_allowed()
    adapter, tool_def = await get_adapter(session, store_entry["tool"])

    # Load per-session overrides from DB (keyed by session id).
    sid = payload.session_id
    overrides = await _load_overrides(session, {**store_entry, "sid": sid})

    # Build base argv from adapter; splice model_flag right after argv_prefix.
    argv = list(adapter.exec_cmd(payload.text, resume_token=store_entry.get("tool_resume_token")))
    if overrides.get("model", "").startswith("-"):
        # Defense in depth: a value starting with '-' would be parsed as another flag.
        logger.warning("session %s has a model override starting with '-'; ignoring", sid)
        overrides.pop("model")
    if "model" in overrides and tool_def.model_flag:
        prefix_len = len(tool_def.argv_prefix)
        argv = argv[:prefix_len] + [tool_def.model_flag, overrides["model"]] + argv[prefix_len:]
    elif "model" in overrides and not tool_def.model_flag:
        logger.warning(
            "session %s has model override but tool %s has null model_flag — skipping injection",
            sid,
            tool_def.name,
        )

    # SECURITY (FIX 1): never spread raw os.environ — that leaks every runner
    # secret (DISCORD_TOKEN, DB DSN, Redis URL, provider keys) into the agent
    # subprocess, which can echo `env` back to Discord. The child env is built
    # from the SCRUBBED per-session overlay (created by saga_create via
    # scrub_env). On resume/recover the overlay is empty, so reconstruct it
    # deterministically with the same scrub_env(...) the create saga uses.
    overlay = store_entry.get("env") or {}
    if overlay:
        env = dict(overlay)
    else:
        env = scrub_env(
            dict(_os.environ),
            extra_allowed=frozenset(adapter.env_allowlist()),
        )
    base_path = env.get("PATH", "")
    extras_raw = list(adapter.extra_paths()) + list(_DEFAULT_EXTRA_PATHS)
    extras = _normalize_extra_paths(extras_raw)
    env["PATH"] = ":".join([*extras, base_path])

    # Apply api_key / base_url overrides into env (override runner-level env).
    if "api_key" in overrides and tool_def.api_key_env:
        env[tool_def.api_key_env] = overrides["api_key"]
    if "base_url" in overrides and tool_def.base_url_env:
        # SECURITY (FIX 2): validate against SSRF before applying. On rejection,
        # log and skip the override (leave runner-default base_url in place)
        # rather than letting the agent target loopback/private/metadata hosts.
        candidate = overrides["base_url"]
        if await asyncio.to_thread(is_base_url_allowed, candidate):
            env[tool_def.base_url_env] = candidate
        else:
            logger.warning(
                "session %s base_url override rejected (SSRF guard)",
                sid,
            )

    # Resolve argv[0] to an absolute path so create_subprocess_exec doesn't
    # rely on its own PATH lookup (which on some platforms ignores env=...).
    resolved = _shutil.which(argv[0], path=env["PATH"])
    if resolved:
        argv[0] = resolved

    from discode.config import settings

    return ExecSession(
        argv=argv,
        cwd=store_entry["cwd"],
        env=env,
        use_pty=adapter.exec_uses_pty(),
        # Keep the full result in memory (JSON extractors need all of it); Discord
        # posting is separately rate-limited by the per-session output quota.
        max_output_bytes=max(1024 * 1024, settings.MAX_OUTPUT_BYTES_PER_SESSION_PER_MIN),
    )


_REPLY_CLIENT_SINGLETON: ReplyClient | None = None


def get_reply_client() -> ReplyClient:
    global _REPLY_CLIENT_SINGLETON
    if _REPLY_CLIENT_SINGLETON is None:
        from discode.config import settings
        from discode.runner.reply_client import ReplyClient

        _REPLY_CLIENT_SINGLETON = ReplyClient(
            base_url=f"http://{settings.RUNNER_CONTROL_BIND}", bearer=settings.RUNNER_CONTROL_AUTH
        )
    return _REPLY_CLIENT_SINGLETON


async def close_reply_client() -> None:
    global _REPLY_CLIENT_SINGLETON
    if _REPLY_CLIENT_SINGLETON is not None:
        await _REPLY_CLIENT_SINGLETON.close()
        _REPLY_CLIENT_SINGLETON = None


async def _deliver(
    payload: InputJobPayload,
    store_entry: dict[str, Any],
    db_factory: async_sessionmaker[AsyncSession],
) -> tuple[str, str | None]:
    """Execute once; return the durable result without performing network delivery."""
    from discode.security.execution_policy import ensure_execution_allowed
    from discode.tools.exec_session import ExecFailed, ExecTimeout

    ensure_execution_allowed()
    try:
        async with db_factory() as db:
            execution = await build_exec_session(store_entry, payload, db)
            adapter, _ = await get_adapter(db, store_entry["tool"])

        async def monitor() -> None:
            while True:
                await asyncio.sleep(_REVOCATION_POLL_SECONDS)
                ensure_execution_allowed()
                try:
                    async with db_factory() as check_db:
                        valid = await check_db.scalar(
                            text(
                                "SELECT 1 FROM sessions WHERE id=:sid "
                                "AND lifecycle_version=:version "
                                "AND status IN ('running','idle')"
                            ),
                            {
                                "sid": payload.session_id,
                                "version": store_entry["lifecycle_version"],
                            },
                        )
                except Exception:
                    # A DB blip or pool timeout is not a revocation; keep the
                    # already-authorized turn running and check again.
                    logger.warning(
                        "revocation check failed for session %s; retrying",
                        payload.session_id,
                        exc_info=True,
                    )
                    continue
                if not valid:
                    raise RuntimeError("session_execution_revoked")

        run_task = asyncio.create_task(execution.run(timeout=_EXEC_TIMEOUT_SECONDS))
        monitor_task = asyncio.create_task(monitor())
        try:
            done, _ = await asyncio.wait(
                (run_task, monitor_task), return_when=asyncio.FIRST_COMPLETED
            )
            if monitor_task in done:
                monitor_task.result()
            result = run_task.result()
        finally:
            run_task.cancel()
            monitor_task.cancel()
            await asyncio.gather(run_task, monitor_task, return_exceptions=True)
    except AmbiguousPrompt:
        return (
            "⚠️ This tool would read a message starting with `-` as a command-line flag. "
            "Rephrase it so it doesn't start with `-` (e.g. `Please: - fix X`).",
            None,
        )
    except BadSessionConfig:
        return "⚠️ Session configuration is unreadable. Ask an administrator to reset it.", None
    except ExecTimeout:
        return (
            "⚠️ Tool timed out. It may have changed files; inspect the workspace before retrying.",
            None,
        )
    except ExecFailed as exc:
        logger.warning("tool failed session=%s exit=%s", payload.session_id, exc.exit_code)
        return (
            f"⚠️ Tool exited with status {exc.exit_code}. Inspect the workspace before retrying.",
            None,
        )
    except Exception as exc:
        logger.warning(
            "tool execution failed session=%s type=%s", payload.session_id, type(exc).__name__
        )
        return "⚠️ Tool execution failed. Inspect the workspace before retrying.", None
    reply = _strip_ansi(adapter.extract_reply(result.text, result.stderr)).strip()
    if not reply:
        reply = _strip_ansi(result.stderr or "").strip() or "⚠️ Tool produced no output."
    if len(reply) > _MAX_REPLY_CHARS:
        # The reply endpoint rejects longer text (422), which would never deliver.
        reply = reply[:_MAX_REPLY_CHARS] + f"\n[reply truncated at {_MAX_REPLY_CHARS} characters]"
    return reply, adapter.parse_resume_token(result.text, result.stderr)


@dataclass
class InputJobPayload:
    session_id: str
    guild_id: str
    thread_id: str
    host_id: str
    text: str
    idempotency_key: str
    lifecycle_version: int | None = None


async def run_input_job(
    payload: InputJobPayload,
    *,
    session_store: dict[str, dict[str, Any]],
    db_factory: async_sessionmaker[AsyncSession],
) -> str:
    """Fence turns per session, persist results, and retry delivery without re-execution.

    The advisory transaction owns no session row locks. Its dedicated connection
    stays alive through execution; a crashed execution remains explicitly uncertain.
    Lifecycle writers use a version CAS, so old turns cannot restore old tokens.
    """
    async with db_factory() as lock_db, lock_db.begin():
        locked = await lock_db.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:sid, 0))"),
            {"sid": payload.session_id},
        )
        if not locked:
            return "deferred"
        return await _run_locked(payload, session_store, db_factory)


async def _run_locked(
    payload: InputJobPayload,
    session_store: dict[str, dict[str, Any]],
    db_factory: async_sessionmaker[AsyncSession],
) -> str:
    from discode.runner.saga_create import revalidate_cwd
    from discode.security.execution_policy import ensure_execution_allowed

    sid, ikey = payload.session_id, payload.idempotency_key
    async with db_factory() as db:
        row = (
            (
                await db.execute(
                    text("""
            SELECT tool, cwd_realpath, tool_resume_token, guild_id, thread_id,
                   host_id, status, owner_id, lifecycle_version
            FROM sessions WHERE id=:sid
        """),
                    {"sid": sid},
                )
            )
            .mappings()
            .first()
        )
        if row is None or row["status"] not in ("running", "idle"):
            return "aborted"
        if str(row["guild_id"]) != payload.guild_id or str(row["host_id"]) != payload.host_id:
            return "aborted"
        version = row["lifecycle_version"]
        if payload.lifecycle_version is not None and payload.lifecycle_version != version:
            return "aborted"
        turn = (
            (
                await db.execute(
                    text("SELECT * FROM input_turns WHERE idempotency_key=:ikey"), {"ikey": ikey}
                )
            )
            .mappings()
            .first()
        )
        earliest = await db.scalar(
            text("""
            SELECT idempotency_key FROM input_turns
            WHERE session_id=:sid AND lifecycle_version=:version
              AND status IN ('queued','executing','completed','uncertain')
            ORDER BY sequence LIMIT 1
        """),
            {"sid": sid, "version": version},
        )
        if earliest is not None and earliest != ikey:
            return "deferred"
        if turn is not None and (
            str(turn["session_id"]) != sid or turn["lifecycle_version"] != version
        ):
            return "aborted"
        if turn is not None and turn["status"] != "queued":
            if turn["status"] in ("delivered", "delivery_failed", "aborted"):
                # Terminal: a redelivered job (e.g. its XACK failed) must not repost.
                return "skipped_replay"
            if turn["status"] in ("executing", "uncertain"):
                # No lock owner remains, but external side effects cannot be rolled back.
                await db.execute(
                    text("""
                    UPDATE input_turns SET status='uncertain', updated_at=now()
                    WHERE idempotency_key=:ikey
                """),
                    {"ikey": ikey},
                )
                await db.commit()
                await db.close()
                try:
                    await get_reply_client().post_reply(
                        sid,
                        "⚠️ The previous execution was interrupted; its effects are uncertain. "
                        "Inspect the workspace, then restart this session to continue.",
                        idempotency_key=ikey + ":uncertain",
                        lifecycle_version=version,
                    )
                except ReplyError as exc:
                    # 410: the notice can never be posted; the turn stays uncertain
                    # (restart is the way out) but this job must not loop forever.
                    return "aborted" if exc.status == 410 else "deferred"
                except Exception:
                    return "deferred"
                return "aborted"
        else:
            realpath, denied = await revalidate_cwd(
                db,
                cwd=row["cwd_realpath"] or "",
                guild_id=payload.guild_id,
                owner_id=row["owner_id"],
                host_id=payload.host_id,
            )
            if denied:
                await db.close()
                # Retire the turn so it doesn't block later turns forever, and tell the user.
                await _mark_turn(db_factory, ikey, "aborted", only_if="queued")
                with contextlib.suppress(Exception):
                    await get_reply_client().post_reply(
                        sid,
                        "⚠️ This session's workspace is no longer an allowed root; "
                        "input was not run. Ask an admin to re-allow it, then restart.",
                        idempotency_key=ikey + ":cwd_denied",
                        lifecycle_version=version,
                    )
                return "aborted"
            ensure_execution_allowed()
            entry = dict(row)
            entry.update(cwd=realpath, env={}, sid=sid)
            session_store[sid] = entry
    if turn is None or turn["status"] == "queued":
        # Persist intent before subprocess side effects. A lost response here is
        # conservative: next delivery reports uncertainty instead of rerunning.
        async with db_factory() as db, db.begin():
            admitted = await db.scalar(
                text("""
                UPDATE sessions SET last_activity_at=now(), status='running'
                WHERE id=:sid AND lifecycle_version=:version AND status IN ('running','idle')
                RETURNING id
            """),
                {"sid": sid, "version": version},
            )
            if admitted is None:
                return "aborted"
            await db.execute(
                text("""
                INSERT INTO input_turns(idempotency_key,session_id,lifecycle_version,status)
                VALUES (:ikey,:sid,:version,'executing')
                ON CONFLICT (idempotency_key) DO UPDATE SET status='executing',updated_at=now()
                WHERE input_turns.status='queued' AND input_turns.session_id=EXCLUDED.session_id
                  AND input_turns.lifecycle_version=EXCLUDED.lifecycle_version
            """),
                {"ikey": ikey, "sid": sid, "version": version},
            )
        reply, token = await _deliver(payload, entry, db_factory)
        async with db_factory() as db, db.begin():
            await db.execute(
                text("""
                UPDATE input_turns SET status='completed',result_text=:reply,
                    resume_token=:token,updated_at=now() WHERE idempotency_key=:ikey
            """),
                {"reply": reply, "token": token, "ikey": ikey},
            )
            await db.execute(
                text("""
                UPDATE sessions SET tool_resume_token=COALESCE(:token,tool_resume_token),
                    last_activity_at=now()
                WHERE id=:sid AND lifecycle_version=:version AND status IN ('running','idle')
            """),
                {"sid": sid, "version": version, "token": token},
            )
    else:
        reply = turn["result_text"]
    try:
        await get_reply_client().post_reply(
            sid, reply, idempotency_key=ikey, lifecycle_version=version
        )
    except ReplyError as exc:
        if exc.status in (410, 422):
            # Can never be delivered: Discord rejected it (thread deleted, access lost)
            # or the reply endpoint rejected the body.
            # Retire the turn so it stops blocking later turns and janitor sweeps.
            logger.warning("reply undeliverable session=%s detail=%s", sid, exc)
            await _mark_turn(db_factory, ikey, "delivery_failed")
            return "aborted"
        logger.warning("reply deferred session=%s status=%s", sid, exc.status)
        return "deferred"
    except Exception as exc:
        logger.warning("reply deferred session=%s type=%s", sid, type(exc).__name__)
        return "deferred"
    await _mark_turn(db_factory, ikey, "delivered")
    return "delivered"


async def _mark_turn(
    db_factory: async_sessionmaker[AsyncSession],
    ikey: str,
    status: str,
    *,
    only_if: str | None = None,
) -> None:
    async with db_factory() as db, db.begin():
        await db.execute(
            text(
                "UPDATE input_turns SET status=:status,updated_at=now() "
                "WHERE idempotency_key=:ikey "
                "AND (CAST(:only_if AS text) IS NULL OR status=:only_if)"
            ),
            {"ikey": ikey, "status": status, "only_if": only_if},
        )
