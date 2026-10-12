"""Service helpers backing the /tool slash commands.

Mirrors the structure of setup_service.py: dataclass results, AsyncSession
bound, admin-role gate via is_guild_admin_authorized.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from discode.db.models import Session as SessionModel
from discode.db.models import ToolDefinition
from discode.tools.schema import ToolConfig

_IMMUTABLE_FIELDS = {"name", "enabled", "created_by", "created_at", "updated_at"}
_MUTABLE_FIELDS = frozenset(ToolConfig.model_fields)
_LIST_FIELDS = {
    "argv_prefix",
    "argv_resume_tokens",
    "argv_suffix",
    "extra_paths",
    "env_passthrough",
}
_BOOL_FIELDS = {"uses_pty"}

_UNAUTH_MSG = "Only a configured platform administrator may modify global tools."


@dataclass
class RegisterToolResult:
    authorized: bool
    success: bool
    message: str


@dataclass
class ListToolsResult:
    tools: list[ToolDefinition]


@dataclass
class ShowToolResult:
    tool: ToolDefinition | None
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


def _validate_config(name: str, config: dict[str, Any]) -> str | None:
    from discode.tools.schema import validate_tool_config

    return validate_tool_config(config, name=name)


def _platform_authorized(user_id: str | None) -> bool:
    from discode.config import settings

    return user_id is not None and user_id in settings.platform_admin_user_ids


async def register_tool(
    session: AsyncSession,
    *,
    guild_id: str,
    name: str,
    config: dict[str, Any],
    created_by: str,
    member_roles: list[str],
    user_id: str | None = None,
    member_role_names: list[str] | None = None,
) -> RegisterToolResult:
    if not _platform_authorized(user_id or created_by):
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
        enabled=False,
        **ToolConfig.model_validate(config).model_dump(),
    )
    session.add(row)
    return RegisterToolResult(
        True, True, f"Registered tool '{name}' disabled. Probe it, then use /tool enable."
    )


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
    user_id: str | None = None,
    member_role_names: list[str] | None = None,
) -> UpdateFieldResult:
    if not _platform_authorized(user_id):
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
        normalized = str(value).strip().lower()
        if normalized not in {"true", "false", "1", "0", "yes", "no"}:
            return UpdateFieldResult(True, False, f"field '{field}' expects a boolean")
        parsed = normalized in {"true", "1", "yes"}

    candidate = {key: getattr(row, key) for key in _MUTABLE_FIELDS}
    candidate[field] = parsed
    err = _validate_config(row.name, candidate)
    if err:
        return UpdateFieldResult(True, False, f"resulting config invalid: {err}")

    setattr(row, field, parsed)
    return UpdateFieldResult(True, True, f"Updated {name}.{field}.")


async def _set_enabled(
    session: AsyncSession,
    *,
    guild_id: str,
    name: str,
    enabled: bool,
    member_roles: list[str],
    user_id: str | None = None,
    member_role_names: list[str] | None = None,
) -> EnableDisableResult:
    if not _platform_authorized(user_id):
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


async def enable_tool(session: AsyncSession, **kw: Any) -> EnableDisableResult:
    return await _set_enabled(session, enabled=True, **kw)


async def disable_tool(session: AsyncSession, **kw: Any) -> EnableDisableResult:
    return await _set_enabled(session, enabled=False, **kw)


async def remove_tool(
    session: AsyncSession,
    *,
    guild_id: str,
    name: str,
    member_roles: list[str],
    user_id: str | None = None,
    member_role_names: list[str] | None = None,
) -> RemoveResult:
    if not _platform_authorized(user_id):
        return RemoveResult(False, False, _UNAUTH_MSG)

    row = await session.get(ToolDefinition, name)
    if row is None:
        return RemoveResult(True, False, f"unknown tool '{name}'")

    # Failed/orphaned sessions can never run the tool again; archived and stopped
    # ones can still be resumed or restarted.
    refs = await session.scalar(
        select(func.count())
        .select_from(SessionModel)
        .where(
            SessionModel.tool == name,
            SessionModel.status.not_in(["failed", "orphaned"]),
        )
    )
    if refs and refs > 0:
        return RemoveResult(
            True,
            False,
            f"{refs} live or resumable session(s) still use '{name}'; "
            "disable the tool and stop those sessions first",
        )

    await session.delete(row)
    return RemoveResult(True, True, f"Removed tool '{name}'.")
