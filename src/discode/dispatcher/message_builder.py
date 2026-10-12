from __future__ import annotations

import re

MAX_DISCORD_MESSAGE_LEN = 2000


_FENCE_LINE = re.compile(r"^[ \t]*```([\w+#.-]*)", re.MULTILINE)
_CLOSE_FENCE = "\n```"


def _open_fence(text: str) -> str | None:
    """Return the opening fence (e.g. ```` ```python ````) left unclosed at the end of text."""
    opener: str | None = None
    for match in _FENCE_LINE.finditer(text):
        # A bare ``` closes an open block; anything else opens one.
        opener = None if opener is not None else "```" + match.group(1)[:20]
    return opener


def _split_point(content: str, limit: int, floor: int) -> int:
    # Split at newline boundary if possible; past ``floor`` so each chunk makes progress.
    split_at = content.rfind("\n", 0, limit)
    return split_at if split_at > floor else limit


def build_message(content: str) -> list[str]:
    """Split content into <=2000 char chunks for Discord.

    A code block cut by a split is closed at the end of its chunk and reopened
    (with its language tag) at the start of the next, so formatting survives.
    """
    chunks = []
    reopened = 0  # length of a fence line re-added at the start of ``content``
    while len(content) > MAX_DISCORD_MESSAGE_LEN:
        split_at = _split_point(content, MAX_DISCORD_MESSAGE_LEN, reopened)
        fence = _open_fence(content[:split_at])
        if fence is not None:
            # Leave room for the closing fence.
            split_at = _split_point(content, MAX_DISCORD_MESSAGE_LEN - len(_CLOSE_FENCE), reopened)
            fence = _open_fence(content[:split_at])
        chunk, content = content[:split_at], content[split_at:]
        reopened = 0
        if fence is not None:
            chunk += _CLOSE_FENCE
            content = fence + ("" if content.startswith("\n") else "\n") + content
            reopened = len(fence)
        chunks.append(chunk)
    if content:
        chunks.append(content)
    return chunks


def apply_pattern_redaction(content: str) -> str:
    """Remove common secret patterns from outbound content."""
    # Token pattern redaction
    content = re.sub(r"(?i)(token|secret|key|password)\s*[:=]\s*\S+", r"\1=[REDACTED]", content)
    return content
