"""Terminal text normalization shared by adapters and the runner."""

import re

# OSC terminated by BEL or ST, CSI, and remaining single-character escapes.
_ANSI_RE = re.compile(r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])")


def strip_ansi(value: str) -> str:
    return _ANSI_RE.sub("", value)
