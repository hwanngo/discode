"""Service helpers backing the /tool slash commands.

Mirrors the structure of setup_service.py: dataclass results, AsyncSession
bound, admin-role gate via is_guild_admin_authorized.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from discode.bot.policies.admin_auth import is_guild_admin_authorized
from discode.db.models import Session as SessionModel
from discode.db.models import ToolDefinition
from discode.tools.generic import _VALID_REPLY_EXTRACTORS, _VALID_RESUME_SOURCES

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_IMMUTABLE_FIELDS = {"name", "enabled", "created_by", "created_at", "updated_at"}
_MUTABLE_FIELDS = {
    "display_name",
    "argv_prefix",
    "argv_resume_tokens",
    "argv_suffix",
    "uses_pty",
    "resume_token_source",
    "resume_token_pattern",
    "reply_extractor",
    "reply_json_field",
    "extra_paths",
    "env_passthrough",
    "model_flag",
    "api_key_env",
    "base_url_env",
}
_LIST_FIELDS = {
    "argv_prefix",
    "argv_resume_tokens",
    "argv_suffix",
    "extra_paths",
    "env_passthrough",
}
_BOOL_FIELDS = {"uses_pty"}

_UNAUTH_MSG = "You need the configured admin role to use this command."


@dataclass
class RegisterToolResult:
    authorized: bool
    success: bool
    message: str


@dataclass
class ListToolsResult:
    tools: list


@dataclass
class ShowToolResult:
    tool: object | None
    message: str


@dataclass
class UpdateFieldResult:
    authorized: bool
    success: bool
    message: str


@dataclass
class EnableDisableResult:
    authorized: bool
    success: bool
    enabled: bool
    message: str


@dataclass
class RemoveResult:
    authorized: bool
    success: bool
    message: str


def _validate_config(name: str, config: dict) -> str | None:
    """Return error message or None if valid."""
    if not _NAME_RE.match(name):
        return f"name must match {_NAME_RE.pattern}"
    if not isinstance(config.get("argv_prefix"), list) or not config["argv_prefix"]:
        return "argv_prefix must be non-empty list"
    if not isinstance(config.get("argv_suffix"), list) or not any(
        "{prompt}" in t for t in config["argv_suffix"]
    ):
        return "argv_suffix must contain {prompt} placeholder"
    rt = config.get("argv_resume_tokens") or []
    if (
        rt
        and not any("{token}" in t for t in rt)
        and config.get("resume_token_source") != "sentinel"
    ):
        return "argv_resume_tokens must contain {token} unless resume_token_source=sentinel"
    if config.get("resume_token_source") not in _VALID_RESUME_SOURCES:
        return f"resume_token_source must be one of {sorted(_VALID_RESUME_SOURCES)}"
    if config.get("reply_extractor") not in _VALID_REPLY_EXTRACTORS:
        return f"reply_extractor must be one of {sorted(_VALID_REPLY_EXTRACTORS)}"
    if config["resume_token_source"] not in {"none", "sentinel"} and not config.get(
        "resume_token_pattern"
    ):
        return "resume_token_source requires resume_token_pattern"
    if config["resume_token_source"] in {"stdout_regex", "stderr_regex"}:
        try:
            re.compile(config["resume_token_pattern"])
        except re.error as e:
            return f"resume_token_pattern regex invalid: {e}"
    if config["reply_extractor"] == "json_field" and not config.get("reply_json_field"):
        return "reply_extractor=json_field requires reply_json_field"
    return None


async def register_tool(
    session: AsyncSession,
    *,
    guild_id: str,
    name: str,
    config: dict,
    created_by: str,
    member_roles: list[str],
    member_role_names: list[str] | None = None,
) -> RegisterToolResult:
    if not await is_guild_admin_authorized(
        session,
        guild_id=guild_id,
        member_roles=member_roles,
        member_role_names=member_role_names,
    ):
        return RegisterToolResult(False, False, _UNAUTH_MSG)

    err = _validate_config(name, config)
    if err:
        return RegisterToolResult(True, False, err)

    existing = await session.get(ToolDefinition, name)
    if existing is not None:
        return RegisterToolResult(
            True,
            False,
            f"tool '{name}' already exists; use /tool update or /tool remove",
        )

    row = ToolDefinition(
        name=name,
        created_by=created_by,
        enabled=True,
        **{k: v for k, v in config.items() if k != "enabled"},
    )
    session.add(row)
    return RegisterToolResult(True, True, f"Registered tool '{name}'.")


async def list_tools(session: AsyncSession) -> ListToolsResult:
    rows = (await session.scalars(select(ToolDefinition).order_by(ToolDefinition.name))).all()
    return ListToolsResult(list(rows))


async def show_tool(session: AsyncSession, *, name: str) -> ShowToolResult:
    row = await session.get(ToolDefinition, name)
    return ShowToolResult(row, "" if row else f"unknown tool '{name}'")


async def update_tool_field(
    session: AsyncSession,
    *,
    guild_id: str,
    name: str,
    field: str,
    value: object,
    member_roles: list[str],
    member_role_names: list[str] | None = None,
) -> UpdateFieldResult:
    if not await is_guild_admin_authorized(
        session,
        guild_id=guild_id,
        member_roles=member_roles,
        member_role_names=member_role_names,
    ):
        return UpdateFieldResult(False, False, _UNAUTH_MSG)

    if field in _IMMUTABLE_FIELDS:
        return UpdateFieldResult(
            True, False, f"field '{field}' is not editable; use enable/disable/remove"
        )
    if field not in _MUTABLE_FIELDS:
        return UpdateFieldResult(True, False, f"unknown field '{field}'")

    row = await session.get(ToolDefinition, name)
    if row is None:
        return UpdateFieldResult(True, False, f"unknown tool '{name}'")

    parsed: object = value
    if field in _LIST_FIELDS:
        try:
            parsed = json.loads(str(value))
            if not isinstance(parsed, list):
                raise ValueError
        except json.JSONDecodeError, ValueError:
            return UpdateFieldResult(True, False, f"field '{field}' expects a JSON list")
    elif field in _BOOL_FIELDS:
        parsed = str(value).strip().lower() in ("true", "1", "yes")

    setattr(row, field, parsed)

    err = _validate_config(
        row.name,
        {
            "argv_prefix": row.argv_prefix,
            "argv_resume_tokens": row.argv_resume_tokens,
            "argv_suffix": row.argv_suffix,
            "resume_token_source": row.resume_token_source,
            "resume_token_pattern": row.resume_token_pattern,
            "reply_extractor": row.reply_extractor,
            "reply_json_field": row.reply_json_field,
        },
    )
    if err:
        return UpdateFieldResult(True, False, f"resulting config invalid: {err}")

    return UpdateFieldResult(True, True, f"Updated {name}.{field}.")


async def _set_enabled(
    session: AsyncSession,
    *,
    guild_id: str,
    name: str,
    enabled: bool,
    member_roles: list[str],
    member_role_names: list[str] | None = None,
) -> EnableDisableResult:
    if not await is_guild_admin_authorized(
        session,
        guild_id=guild_id,
        member_roles=member_roles,
        member_role_names=member_role_names,
    ):
        return EnableDisableResult(False, False, False, _UNAUTH_MSG)

    row = await session.get(ToolDefinition, name)
    if row is None:
        return EnableDisableResult(True, False, False, f"unknown tool '{name}'")

    row.enabled = enabled
    return EnableDisableResult(
        True,
        True,
        enabled,
        f"Tool '{name}' {'enabled' if enabled else 'disabled'}.",
    )


async def enable_tool(session: AsyncSession, **kw) -> EnableDisableResult:
    return await _set_enabled(session, enabled=True, **kw)


async def disable_tool(session: AsyncSession, **kw) -> EnableDisableResult:
    return await _set_enabled(session, enabled=False, **kw)


async def remove_tool(
    session: AsyncSession,
    *,
    guild_id: str,
    name: str,
    member_roles: list[str],
    member_role_names: list[str] | None = None,
) -> RemoveResult:
    if not await is_guild_admin_authorized(
        session,
        guild_id=guild_id,
        member_roles=member_roles,
        member_role_names=member_role_names,
    ):
        return RemoveResult(False, False, _UNAUTH_MSG)

    row = await session.get(ToolDefinition, name)
    if row is None:
        return RemoveResult(True, False, f"unknown tool '{name}'")

    refs = await session.scalar(
        select(func.count()).select_from(SessionModel).where(SessionModel.tool == name)
    )
    if refs and refs > 0:
        return RemoveResult(
            True,
            False,
            f"{refs} session(s) still reference '{name}'; disable + archive sessions first",
        )

    await session.delete(row)
    return RemoveResult(True, True, f"Removed tool '{name}'.")
