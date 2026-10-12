"""Enforce explicit host trust and an operator-controlled emergency stop."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from discode.config import Settings


class ExecutionDenied(ValueError):
    pass


def ensure_outbound_allowed(settings: Settings | None = None) -> None:
    if settings is None:
        from discode.config import get_settings

        settings = get_settings()
    if settings.EMERGENCY_STOP:
        raise ExecutionDenied("emergency_stop: side effects are disabled")


def ensure_execution_allowed(settings: Settings | None = None) -> None:
    if settings is None:
        from discode.config import get_settings

        settings = get_settings()
    ensure_outbound_allowed(settings)
    if settings.SESSION_ISOLATION_MODE != "trusted_host":
        raise ExecutionDenied(
            "unsupported_isolation_mode: no OS sandbox is implemented; use a dedicated "
            "disposable host with trusted users and explicitly select trusted_host"
        )
