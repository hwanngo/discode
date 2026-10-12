"""Shared logging setup for every Discode process."""

from __future__ import annotations

import logging

# Attributes every LogRecord has; anything else came from ``extra=``.
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys() | {"message", "asctime"}
)


class ExtraFormatter(logging.Formatter):
    """Append ``extra=`` fields (session ids, counts) as ``key=value`` pairs."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extras = {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}
        if extras:
            line += " " + " ".join(f"{k}={v}" for k, v in sorted(extras.items()))
        return line


def configure_logging() -> None:
    """Configure the root logger from validated ``LOG_LEVEL`` (dotenv-aware)."""
    from discode.config import get_settings

    handler = logging.StreamHandler()
    handler.setFormatter(ExtraFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=get_settings().LOG_LEVEL, handlers=[handler], force=True)
