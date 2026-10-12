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


# Key names whose assigned value is treated as secret. Lower-case words match in
# any identifier (``api_key``, ``db_password``); AUTH/KEY only in UPPER_SNAKE env
# names (``RUNNER_CONTROL_AUTH``, ``DEPLOYMENT_SECRET_KEY``) to spare prose/code.
_SECRET_KEY_NAME = (
    r"(?:[A-Za-z0-9_.-]*(?i:token|secret|passw(?:or)?d|api[_-]?key|credentials?|private[_-]?key)"
    r"[A-Za-z0-9_.-]*"
    r"|[A-Z0-9_]*(?:_AUTH|_KEY|AUTH_)[A-Z0-9_]*)"
)

# Common secret patterns (defense in depth at dispatcher). Patterns with a
# ``keep`` group preserve that prefix and redact the rest of the match.
COMMON_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # KEY=value, KEY: value, "key": "value", key = 'value'
    re.compile(rf"""(?P<keep>["']?\b{_SECRET_KEY_NAME}["']?\s*[:=]\s*)["']?[^\s"',;}}]+["']?"""),
    re.compile(r"(?i)(?P<keep>bearer\s+)[A-Za-z0-9\-._~+/]+=*"),
    # Database URLs with embedded user:password@ credentials. Match before the
    # generic patterns so the whole credential portion is redacted at once.
    re.compile(r"(?i)(?P<keep>[a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s:@]+@\S+"),
    # Discord bot tokens: three dot-separated base64url segments, appearing bare.
    re.compile(r"[MNO][A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{27,}"),
    # Anthropic / OpenAI (incl. sk-proj-...) keys; bodies contain '-' and '_'.
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    # GitHub tokens.
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    # AWS access key ids, Google API keys, Slack tokens.
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    # PEM private key blocks.
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
        re.DOTALL,
    ),
]


def _redact_match(m: re.Match[str]) -> str:
    keep = m.groupdict().get("keep")
    return f"{keep}[REDACTED]" if keep else "[REDACTED]"


_deployment_secret_patterns: list[re.Pattern[str]] | None = None


def _deployment_secrets() -> list[re.Pattern[str]]:
    """Exact values of this deployment's own secrets, whatever their surrounding text."""
    global _deployment_secret_patterns
    if _deployment_secret_patterns is None:
        values: set[str] = set()
        try:
            from urllib.parse import urlsplit

            from discode.config import get_settings

            settings = get_settings()
            values.add(settings.DISCORD_TOKEN)
            values.add(settings.RUNNER_CONTROL_AUTH)
            values.update(k.strip() for k in settings.DEPLOYMENT_SECRET_KEY.split(","))
            for url in (settings.DATABASE_URL, settings.REDIS_URL):
                if password := urlsplit(url).password:
                    values.add(password)
        except Exception:
            # Settings unavailable (e.g. tests without env); pattern rules still apply.
            return []
        # Short values would redact ordinary words.
        _deployment_secret_patterns = [
            re.compile(re.escape(v)) for v in sorted(values, key=len, reverse=True) if len(v) >= 8
        ]
    return _deployment_secret_patterns


def apply_common_redaction(text: str) -> str:
    """Redact this deployment's secrets and common secret formats."""
    for pattern in _deployment_secrets():
        text = pattern.sub("[REDACTED]", text)
    for pattern in COMMON_SECRET_PATTERNS:
        text = pattern.sub(_redact_match, text)
    return text
