from __future__ import annotations

import base64
import re


def build_redaction_patterns(hook_secret: bytes) -> list[re.Pattern[str]]:
    """Build regex patterns for all encodings of the hook secret."""
    patterns = [
        re.compile(re.escape(hook_secret.decode("latin-1", errors="replace"))),
        re.compile(re.escape(hook_secret.hex())),
        re.compile(re.escape(base64.b64encode(hook_secret).decode())),
    ]
    return patterns


def redact_value(text: str, patterns: list[re.Pattern[str]]) -> str:
    """Apply all redaction patterns to text."""
    for pattern in patterns:
        text = pattern.sub("[REDACTED]", text)
    return text


# Common secret patterns (defense in depth at dispatcher)
COMMON_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)(token|secret|password|api[_-]?key)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"),
    # Database URLs with embedded user:password@ credentials. Match before the
    # generic patterns so the whole credential portion is redacted at once.
    re.compile(r"(?i)([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s:@]+@\S+"),
    # Discord bot tokens: three dot-separated base64url segments, appearing bare.
    re.compile(r"[MNO][A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{27,}"),
    # Anthropic API keys. Must precede the broad sk- rule so the full key body
    # (which contains dashes) is redacted rather than truncated at the first dash.
    re.compile(r"(?i)sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)sk-[A-Za-z0-9]{20,}"),  # OpenAI-style keys
]


def _redact_match(m: re.Match[str]) -> str:
    s = m.group(0)
    return s.split(":")[0] + ":[REDACTED]" if ":" in s else "[REDACTED]"


def apply_common_redaction(text: str) -> str:
    """Apply common secret pattern redaction."""
    for pattern in COMMON_SECRET_PATTERNS:
        text = pattern.sub(_redact_match, text)
    return text
