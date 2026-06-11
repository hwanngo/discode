"""Per-session override service: /session config set|get|clear|clear-all.

This module is the single source of truth for:
- which override keys exist (the reserved registry)
- how to validate values for each key
- whether each key is a secret (encrypted at rest)

Service helpers (set/get/clear/clear-all) are added in Task 5.
build_exec_session integration is in Task 7.
"""

from __future__ import annotations

import logging
import string
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from discode.bot.policies.admin_auth import is_guild_admin_authorized
from discode.config import settings
from discode.db.models import Session as SessionModel
from discode.db.models import SessionConfig, ToolDefinition
from discode.security.fernet import load_multifernet

logger = logging.getLogger(__name__)

_UNAUTH_MSG = "You need the configured admin role to use this command."


@dataclass(frozen=True)
class KeySpec:
    name: str
    secret: bool
    validator: Callable[[str], str | None]


# Printable ASCII minus vertical tab and form feed (rare; would survive
# argv but cause trouble in logs/Discord).
_PRINTABLE = set(string.printable) - set("\x0b\x0c")


def _validate_model(v: str) -> str | None:
    if not v:
        return "model must be non-empty"
    if len(v) > 128:
        return "model must be ≤ 128 chars"
    if any(c not in _PRINTABLE for c in v):
        return "model must contain only printable ASCII"
    return None


def _validate_base_url(v: str) -> str | None:
    if not v:
        return "base_url must be non-empty"
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https"):
        return "base_url scheme must be http or https"
    if not parsed.netloc:
        return "base_url must have a host"
    return None


def _validate_api_key(v: str) -> str | None:
    if not v:
        return "api_key must be non-empty"
    if len(v) > 1024:
        return "api_key must be ≤ 1024 chars"
    return None


_RESERVED_KEYS: dict[str, KeySpec] = {
    "model": KeySpec("model", secret=False, validator=_validate_model),
    "base_url": KeySpec("base_url", secret=False, validator=_validate_base_url),
    "api_key": KeySpec("api_key", secret=True, validator=_validate_api_key),
}


# ---------------------------------------------------------------------------
# Internal crypto helpers
# ---------------------------------------------------------------------------


def _fernet():
    return load_multifernet(settings.DEPLOYMENT_SECRET_KEY)


def _encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


# ---------------------------------------------------------------------------
# Internal DB helpers
# ---------------------------------------------------------------------------


async def _resolve_session_and_tool(
    session: AsyncSession, session_id: object
) -> tuple[SessionModel | None, ToolDefinition | None]:
    sess = await session.get(SessionModel, session_id)
    if sess is None:
        return None, None
    tool = await session.get(ToolDefinition, sess.tool)
    return sess, tool


def _tool_supports_key(tool: ToolDefinition, key: str) -> bool:
    if key == "model":
        return tool.model_flag is not None
    if key == "api_key":
        return tool.api_key_env is not None
    if key == "base_url":
        return tool.base_url_env is not None
    return False


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SetResult:
    authorized: bool
    success: bool
    message: str


@dataclass
class ConfigRow:
    key: str
    display_value: str  # plaintext for non-secret keys, "<set>" for encrypted keys


@dataclass
class GetResult:
    rows: list[ConfigRow]


@dataclass
class ClearResult:
    authorized: bool
    success: bool
    deleted: bool
    message: str


@dataclass
class ClearAllResult:
    authorized: bool
    success: bool
    count: int
    message: str


# ---------------------------------------------------------------------------
# Service helpers
# ---------------------------------------------------------------------------


async def set_session_config(
    session: AsyncSession,
    *,
    session_id: object,
    key: str,
    value: str,
    member_roles: list[str],
    member_role_names: list[str],
) -> SetResult:
    sess, tool = await _resolve_session_and_tool(session, session_id)
    if sess is None:
        return SetResult(False, False, f"unknown session '{session_id}'")
    if not await is_guild_admin_authorized(
        session,
        guild_id=sess.guild_id,
        member_roles=member_roles,
        member_role_names=member_role_names,
    ):
        return SetResult(False, False, _UNAUTH_MSG)
    spec = _RESERVED_KEYS.get(key)
    if spec is None:
        return SetResult(True, False, f"Unknown key '{key}'. Reserved: {sorted(_RESERVED_KEYS)}.")
    err = spec.validator(value)
    if err:
        return SetResult(True, False, err)
    if tool is None:
        return SetResult(True, False, f"session's tool '{sess.tool}' not in tool_definitions")
    if not _tool_supports_key(tool, key):
        return SetResult(
            True,
            False,
            f"Tool '{tool.name}' doesn't support per-session '{key}' overrides.",
        )

    if spec.secret:
        try:
            ct = _encrypt(value)
        except Exception as exc:
            logger.error("session_config: encrypt failed: %s", exc)
            return SetResult(
                True,
                False,
                "Server can't encrypt the value; check DEPLOYMENT_SECRET_KEY.",
            )
        stmt = (
            pg_insert(SessionConfig)
            .values(
                session_id=session_id,
                key=key,
                value=None,
                value_ciphertext=ct,
            )
            .on_conflict_do_update(
                index_elements=[SessionConfig.session_id, SessionConfig.key],
                set_={"value": None, "value_ciphertext": ct, "updated_at": func.now()},
            )
        )
    else:
        stmt = (
            pg_insert(SessionConfig)
            .values(
                session_id=session_id,
                key=key,
                value=value,
                value_ciphertext=None,
            )
            .on_conflict_do_update(
                index_elements=[SessionConfig.session_id, SessionConfig.key],
                set_={"value": value, "value_ciphertext": None, "updated_at": func.now()},
            )
        )
    await session.execute(stmt)
    return SetResult(True, True, f"Set {key} for session {str(session_id)[:8]}.")


async def get_session_config(session: AsyncSession, *, session_id: object) -> GetResult:
    rows = (
        await session.scalars(
            select(SessionConfig)
            .where(SessionConfig.session_id == session_id)
            .order_by(SessionConfig.key)
        )
    ).all()
    out: list[ConfigRow] = []
    for r in rows:
        spec = _RESERVED_KEYS.get(r.key)
        if spec and spec.secret:
            out.append(ConfigRow(key=r.key, display_value="<set>"))
        else:
            out.append(ConfigRow(key=r.key, display_value=r.value or "<unreadable>"))
    return GetResult(out)


async def clear_session_config_key(
    session: AsyncSession,
    *,
    session_id: object,
    key: str,
    member_roles: list[str],
    member_role_names: list[str],
) -> ClearResult:
    sess = await session.get(SessionModel, session_id)
    if sess is None:
        return ClearResult(False, False, False, f"unknown session '{session_id}'")
    if not await is_guild_admin_authorized(
        session,
        guild_id=sess.guild_id,
        member_roles=member_roles,
        member_role_names=member_role_names,
    ):
        return ClearResult(False, False, False, _UNAUTH_MSG)
    result = await session.execute(
        delete(SessionConfig).where(
            SessionConfig.session_id == session_id,
            SessionConfig.key == key,
        )
    )
    deleted = (result.rowcount or 0) > 0
    msg = f"Cleared '{key}'." if deleted else "Nothing to clear."
    return ClearResult(True, True, deleted, msg)


async def clear_all_session_config(
    session: AsyncSession,
    *,
    session_id: object,
    member_roles: list[str],
    member_role_names: list[str],
) -> ClearAllResult:
    sess = await session.get(SessionModel, session_id)
    if sess is None:
        return ClearAllResult(False, False, 0, f"unknown session '{session_id}'")
    if not await is_guild_admin_authorized(
        session,
        guild_id=sess.guild_id,
        member_roles=member_roles,
        member_role_names=member_role_names,
    ):
        return ClearAllResult(False, False, 0, _UNAUTH_MSG)
    result = await session.execute(
        delete(SessionConfig).where(SessionConfig.session_id == session_id)
    )
    count = result.rowcount or 0
    return ClearAllResult(True, True, count, f"Cleared {count} override(s).")


# ---------------------------------------------------------------------------
# Public decrypt helper (used by build_exec_session in Task 7)
# ---------------------------------------------------------------------------


def decrypt_value_ciphertext(ciphertext: str) -> str:
    """Decrypt a secret SessionConfig row's value_ciphertext.

    Raises cryptography.fernet.InvalidToken on failure — callers turn this into
    a session-scoped error message.
    """
    return _decrypt(ciphertext)
