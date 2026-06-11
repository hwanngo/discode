from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.security.env_scrub import scrub_env
from discode.tools.errors import BadSessionConfig, UnknownTool
from discode.tools.registry import get_adapter

if TYPE_CHECKING:
    from discode.runner.reply_client import ReplyClient

logger = logging.getLogger(__name__)

# Default tentative-claim lease. Overridden at runtime from Settings when
# available (see _lease_seconds). The exec timeout (~300s) can exceed a short
# lease, letting a second consumer reclaim and double-execute; we widen the
# lease to cover the exec window plus margin.
INPUT_TENTATIVE_LEASE_SECONDS = 30

# Max time _deliver's subprocess may run; used to compute a safe lease floor.
_EXEC_TIMEOUT_SECONDS = 300.0
# Margin added on top of the exec timeout so the lease never expires mid-exec.
_LEASE_EXEC_MARGIN_SECONDS = 60


def _lease_seconds() -> int:
    """Effective tentative-claim lease in seconds.

    Reads INPUT_TENTATIVE_LEASE_SECONDS from Settings when present, then raises
    it to at least exec_timeout + margin so a slow turn can't have its lease
    expire and be double-executed by another consumer (FIX 6).
    """
    configured = INPUT_TENTATIVE_LEASE_SECONDS
    try:
        from discode.config import settings

        configured = int(getattr(settings, "INPUT_TENTATIVE_LEASE_SECONDS", configured))
    except Exception:
        # Settings may be unconstructable in tests/CLI; fall back to module default.
        pass
    floor = int(_EXEC_TIMEOUT_SECONDS + _LEASE_EXEC_MARGIN_SECONDS)
    return max(configured, floor)

_TENTATIVE = "runner.send_input.v1#tentative"
_COMMITTED = "runner.send_input.v1#committed"


# CSI / OSC / single-char escape sequences emitted by TUI tools (opencode,
# codex) — must be stripped before posting to Discord.
_ANSI_RE = __import__("re").compile(
    r"\x1B(?:"  # ESC
    r"[@-Z\\-_]"  # 2-byte sequences
    r"|\["  # CSI ...
    r"[0-?]*[ -/]*[@-~]"
    r"|\][^\x07]*\x07"  # OSC ... BEL
    r")"
)


def _strip_ansi(s: str) -> str:
    """Remove ANSI/VT100 escape sequences so chat output renders cleanly."""
    if not s:
        return s
    return _ANSI_RE.sub("", s)


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
    return [p for p in existing if not (p in seen or seen.add(p))]


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


async def _load_overrides(session, store_entry: dict[str, Any]) -> dict[str, str]:
    """Load per-session config overrides from the DB. Test seam (patch this)."""
    from cryptography.fernet import InvalidToken
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
            except (InvalidToken, Exception) as exc:
                raise BadSessionConfig(str(sid), f"{row.key} decryption failed") from exc
    return overrides


async def build_exec_session(store_entry: dict[str, Any], payload: InputJobPayload, session):
    """Construct an ExecSession for one turn. Test seam (patch this)."""
    import os as _os
    import shutil as _shutil

    from discode.tools.exec_session import ExecSession

    adapter, tool_def = await get_adapter(session, store_entry["tool"])

    # Load per-session overrides from DB (keyed by session id).
    sid = store_entry.get("sid")
    overrides = await _load_overrides(session, store_entry)

    # Build base argv from adapter; splice model_flag right after argv_prefix.
    argv = list(adapter.exec_cmd(payload.text, resume_token=store_entry.get("tool_resume_token")))
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
        if is_base_url_allowed(candidate):
            env[tool_def.base_url_env] = candidate
        else:
            logger.warning(
                "session %s base_url override rejected (SSRF guard): %r — skipping",
                sid,
                candidate,
            )

    # Resolve argv[0] to an absolute path so create_subprocess_exec doesn't
    # rely on its own PATH lookup (which on some platforms ignores env=...).
    resolved = _shutil.which(argv[0], path=env["PATH"])
    if resolved:
        argv[0] = resolved

    return ExecSession(
        argv=argv,
        cwd=store_entry["cwd"],
        env=env,
        use_pty=adapter.exec_uses_pty(),
    )


_REPLY_CLIENT_SINGLETON = None


def get_reply_client() -> ReplyClient:
    """Lazy module-level singleton ReplyClient. Test seam (patch this)."""
    global _REPLY_CLIENT_SINGLETON
    if _REPLY_CLIENT_SINGLETON is None:
        import os as _os

        from discode.runner.reply_client import ReplyClient

        bind = _os.environ.get("RUNNER_CONTROL_BIND", "127.0.0.1:8788")
        bearer = _os.environ.get("RUNNER_CONTROL_AUTH", "")
        _REPLY_CLIENT_SINGLETON = ReplyClient(base_url=f"http://{bind}", bearer=bearer)
    return _REPLY_CLIENT_SINGLETON


async def _deliver(
    payload: InputJobPayload,
    store_entry: dict[str, Any],
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Spawn one tool subprocess, capture stdout, POST to the reply API.

    Wraps every step in a try/except chain that surfaces failures back to
    Discord via the reply endpoint, so the user sees *something* rather than
    silence when exec/reply fail.
    """
    from discode.tools.exec_session import ExecFailed, ExecTimeout

    sid8 = payload.session_id[:8]
    reply_client = get_reply_client()

    async def _post_error(prefix: str, detail: str) -> None:
        text_msg = f"⚠️ pipeline error ({prefix}): {detail[:1500]}"
        try:
            await reply_client.post_reply(
                payload.session_id,
                text_msg,
                idempotency_key=payload.idempotency_key + "#err",
            )
        except Exception:
            logger.exception("_deliver: failed to post error reply sid=%s", sid8)

    try:
        async with db_factory() as _reg_session:
            exec_session = await build_exec_session(store_entry, payload, _reg_session)
        env_audit = {
            k: ("<set>" if (exec_session._env or {}).get(k) else "<missing>")
            for k in (
                "HOME",
                "PATH",
                "ANTHROPIC_API_KEY",
                "CLAUDE_CONFIG_DIR",
                "CLAUDE_CODE_OAUTH_TOKEN",
                "OPENAI_API_KEY",
                "CODEX_HOME",
            )
        }
        logger.info(
            "_deliver: spawning sid=%s tool=%s cwd=%s pty=%s argv=%s env_audit=%s",
            sid8,
            store_entry.get("tool"),
            store_entry.get("cwd"),
            getattr(exec_session, "_use_pty", "?"),
            getattr(exec_session, "_argv", []),
            env_audit,
        )
    except BadSessionConfig as exc:
        logger.warning("_deliver: bad session config sid=%s: %s", sid8, exc)
        await _post_error(
            "session_config",
            f"Session config unreadable: {exc.reason}. "
            "Ask an admin to /session config set <key> <new>.",
        )
        return
    except Exception as exc:
        logger.exception("_deliver: build_exec_session failed sid=%s", sid8)
        await _post_error("build_exec_session", repr(exc))
        return

    try:
        result = await exec_session.run(timeout=300.0)
        logger.info(
            "_deliver: exec done sid=%s exit=%s text_len=%d stderr_len=%d",
            sid8,
            result.exit_code,
            len(result.text),
            len(result.stderr),
        )
    except ExecTimeout as exc:
        logger.warning("_deliver: exec timeout sid=%s", sid8)
        await _post_error("exec_timeout", str(exc))
        return
    except ExecFailed as exc:
        logger.warning(
            "_deliver: exec failed sid=%s exit=%s stderr=%s stdout=%s",
            sid8,
            exc.exit_code,
            exc.stderr[:500],
            exc.stdout[:500],
        )
        # Some tools (claude --print) write errors to stdout; surface both.
        cleaned_stderr = _strip_ansi(exc.stderr or "").strip()
        cleaned_stdout = _strip_ansi(getattr(exc, "stdout", "") or "").strip()
        detail_parts: list[str] = []
        if cleaned_stdout:
            detail_parts.append(f"stdout: {cleaned_stdout[:600]}")
        if cleaned_stderr:
            detail_parts.append(f"stderr: {cleaned_stderr[:600]}")
        if not detail_parts:
            detail_parts.append("(no output captured)")
        await _post_error(f"exec_exit_{exc.exit_code}", " | ".join(detail_parts))
        return
    except Exception as exc:
        logger.exception("_deliver: exec raised unexpected sid=%s", sid8)
        await _post_error("exec_unexpected", repr(exc))
        return

    # Let the adapter unwrap structured output (e.g. claude --output-format
    # json -> {result, session_id, ...}) before we strip ANSI / fallback to
    # stderr. Adapters that emit plain text return stdout unchanged.
    async with db_factory() as _reg_session2:
        try:
            adapter, _tool_def = await get_adapter(_reg_session2, store_entry["tool"])
        except UnknownTool as exc:
            logger.warning("_deliver: tool unavailable sid=%s tool=%s", sid8, exc.name)
            await _post_error("unknown_tool", str(exc))
            return
    primary = adapter.extract_reply(result.text, result.stderr)

    # Some TUI tools (opencode run, codex exec) print chat output to stderr
    # in non-interactive mode and only status/banner content to stdout.
    # Treat stderr as a fallback content channel when the primary is empty.
    text_to_send = _strip_ansi(primary).strip()
    if not text_to_send:
        text_to_send = _strip_ansi(result.stderr or "").strip()
    if not text_to_send:
        text_to_send = (
            "⚠️ tool produced no output "
            f"(exit={result.exit_code}). "
            "Check runner logs for the spawn argv + cwd."
        )

    try:
        message_id = await reply_client.post_reply(
            payload.session_id,
            text_to_send,
            idempotency_key=payload.idempotency_key,
        )
        logger.info("_deliver: reply posted sid=%s message_id=%s", sid8, message_id)
    except Exception as exc:
        logger.exception("_deliver: reply post failed sid=%s", sid8)
        # Last-ditch — try the error path (different idempotency key)
        await _post_error("reply_post", repr(exc))
        return

    new_token = adapter.parse_resume_token(result.text, result.stderr)
    if new_token and new_token != store_entry.get("tool_resume_token"):
        store_entry["tool_resume_token"] = new_token
        async with db_factory() as db:
            async with db.begin():
                await db.execute(
                    text("UPDATE sessions SET tool_resume_token = :t WHERE id = :sid"),
                    {"t": new_token, "sid": payload.session_id},
                )
        logger.info("_deliver: persisted resume_token sid=%s", sid8)


@dataclass
class InputJobPayload:
    session_id: str
    guild_id: str
    thread_id: str
    host_id: str
    text: str
    idempotency_key: str


async def run_input_job(
    payload: InputJobPayload,
    *,
    session_store: dict[str, dict[str, Any]],
    db_factory: async_sessionmaker[AsyncSession],
) -> str:
    """
    Execute the input delivery pipeline.

    Returns one of: "delivered", "skipped_replay", "deferred" (live lease), "aborted" (Stage 2.5
    failed).
    """
    owner = str(uuid.uuid4())
    ikey = payload.idempotency_key
    sid = payload.session_id
    lease = _lease_seconds()

    # -------------------------------------------------------------------------
    # Stage 2 — two-step CTE claim
    # -------------------------------------------------------------------------

    # Step A: try INSERT #tentative (each operation uses its own session)
    async with db_factory() as db:
        async with db.begin():
            insert_result = await db.execute(
                text(
                    """
                    INSERT INTO idempotency_keys
                        (envelope_type, idempotency_key, session_id,
                         expires_at, claim_expires_at, claim_owner)
                    VALUES
                        (:etype, :ikey, :sid,
                         now() + interval '30 days',
                         now() + (:lease * interval '1 second'),
                         :owner)
                    ON CONFLICT DO NOTHING
                    RETURNING idempotency_key
                    """
                ),
                {"etype": _TENTATIVE, "ikey": ikey, "sid": sid, "lease": lease, "owner": owner},
            )
            inserted_row = insert_result.fetchone()

    if inserted_row is not None:
        # We got the lease via fresh INSERT — proceed to Stage 2.5
        pass
    else:
        # Row already exists — check existing lease state
        async with db_factory() as db:
            check_result = await db.execute(
                text(
                    """
                    SELECT 1 FROM idempotency_keys
                    WHERE envelope_type = :etype
                      AND idempotency_key = :ikey
                      AND claim_expires_at > now()
                      AND claim_owner != :owner
                    """
                ),
                {"etype": _TENTATIVE, "ikey": ikey, "owner": owner},
            )
            live_other = check_result.fetchone()

        if live_other is not None:
            # Live lease owned by another handler → defer
            return "deferred"

        # Expired or we own it — try RECLAIM
        async with db_factory() as db:
            async with db.begin():
                reclaim_result = await db.execute(
                    text(
                        """
                        UPDATE idempotency_keys
                        SET claim_expires_at = now() + (:lease * interval '1 second'),
                            claim_owner = :owner
                        WHERE envelope_type = :etype
                          AND idempotency_key = :ikey
                          AND claim_expires_at <= now()
                        RETURNING idempotency_key
                        """
                    ),
                    {"etype": _TENTATIVE, "ikey": ikey, "lease": lease, "owner": owner},
                )
                reclaimed = reclaim_result.fetchone()

        if reclaimed is None:
            # Another handler refreshed the lease just before us
            return "deferred"

        # Reclaimed — check if #committed already exists
        async with db_factory() as db:
            committed_check = await db.execute(
                text(
                    """
                    SELECT outcome FROM idempotency_keys
                    WHERE envelope_type = :etype AND idempotency_key = :ikey
                    """
                ),
                {"etype": _COMMITTED, "ikey": ikey},
            )
            committed_row = committed_check.fetchone()

        if committed_row is not None:
            # Already delivered — write skip notice and return
            from discode.queues.outbox import write_outbox

            async with db_factory() as db:
                async with db.begin():
                    await db.execute(
                        text(
                            """
                            INSERT INTO idempotency_keys
                                (envelope_type, idempotency_key, session_id,
                                 expires_at, outcome)
                            VALUES
                                (:etype, :ikey, :sid,
                                 now() + interval '30 days',
                                 'skipped_replay')
                            ON CONFLICT DO NOTHING
                            """
                        ),
                        {"etype": _COMMITTED, "ikey": ikey, "sid": sid},
                    )
                    await write_outbox(
                        db,
                        producer="runner",
                        stream_key="discord:outbound",
                        envelope_type="discord.terminal_notice.v1",
                        payload={
                            "type": "discord.terminal_notice.v1",
                            "idempotency_key": str(uuid.uuid4()),
                            "session_id": sid,
                            "guild_id": payload.guild_id,
                            "thread_id": payload.thread_id,
                            "alert_kind": "input_replay_skipped",
                        },
                    )
            return "skipped_replay"

        # Reclaimed with no committed — proceed to Stage 2.5

    # -------------------------------------------------------------------------
    # Stage 2.5 — mandatory pre-send refresh
    # -------------------------------------------------------------------------
    async with db_factory() as db:
        async with db.begin():
            refresh_result = await db.execute(
                text(
                    """
                    UPDATE idempotency_keys
                    SET claim_expires_at = now() + (:lease * interval '1 second')
                    WHERE envelope_type = :etype
                      AND idempotency_key = :ikey
                      AND claim_owner = :owner
                      AND claim_expires_at > now()
                      AND NOT EXISTS (
                          SELECT 1 FROM idempotency_keys
                          WHERE envelope_type = :committed_etype
                            AND idempotency_key = :ikey
                      )
                    RETURNING idempotency_key
                    """
                ),
                {
                    "etype": _TENTATIVE,
                    "committed_etype": _COMMITTED,
                    "ikey": ikey,
                    "owner": owner,
                    "lease": lease,
                },
            )
            refreshed = refresh_result.fetchone()

    if refreshed is None:
        return "aborted"

    # -------------------------------------------------------------------------
    # Stage 3 — send
    # -------------------------------------------------------------------------
    store_entry = session_store.get(payload.session_id)
    if store_entry is None:
        async with db_factory() as db:
            status_result = await db.execute(
                text("SELECT status FROM sessions WHERE id = :sid"),
                {"sid": payload.session_id},
            )
            status_row = status_result.fetchone()
        db_status = status_row[0] if status_row else None
        if db_status in ("failed", "orphaned", "stopped", None):
            logger.warning(
                "run_input_job: session %s is %s, dropping input",
                payload.session_id,
                db_status,
            )
            return "aborted"
        logger.info(
            "run_input_job: session %s not in store (status=%s), deferring",
            payload.session_id,
            db_status,
        )
        return "deferred"

    logger.info(
        "run_input_job: sid=%s thread=%s text_len=%d",
        payload.session_id[:8],
        payload.thread_id,
        len(payload.text),
    )
    await _deliver(payload, store_entry, db_factory)

    # -------------------------------------------------------------------------
    # Stage 4 — Write #committed
    # -------------------------------------------------------------------------
    async with db_factory() as db:
        async with db.begin():
            await db.execute(
                text(
                    """
                    INSERT INTO idempotency_keys
                        (envelope_type, idempotency_key, session_id,
                         expires_at, outcome)
                    VALUES
                        (:etype, :ikey, :sid,
                         now() + interval '30 days',
                         'delivered')
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"etype": _COMMITTED, "ikey": ikey, "sid": sid},
            )

    return "delivered"
