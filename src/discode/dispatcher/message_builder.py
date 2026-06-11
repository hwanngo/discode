from __future__ import annotations

import re

MAX_DISCORD_MESSAGE_LEN = 2000


def build_message(content: str) -> list[str]:
    """Split content into <=2000 char chunks for Discord."""
    chunks = []
    while len(content) > MAX_DISCORD_MESSAGE_LEN:
        # Split at newline boundary if possible
        split_at = content.rfind("\n", 0, MAX_DISCORD_MESSAGE_LEN)
        if split_at == -1:
            split_at = MAX_DISCORD_MESSAGE_LEN
        chunks.append(content[:split_at])
        content = content[split_at:]
    if content:
        chunks.append(content)
    return chunks


def apply_pattern_redaction(content: str) -> str:
    """Remove common secret patterns from outbound content."""
    # Token pattern redaction
    content = re.sub(r"(?i)(token|secret|key|password)\s*[:=]\s*\S+", r"\1=[REDACTED]", content)
    return content
