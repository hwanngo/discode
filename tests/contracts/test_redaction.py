from __future__ import annotations

import base64

from discode.security.redaction import (
    apply_common_redaction,
    build_redaction_patterns,
    redact_value,
)

SECRET = b"super-secret-key-12345"


def test_build_redaction_patterns_creates_three_patterns() -> None:
    patterns = build_redaction_patterns(SECRET)
    assert len(patterns) == 3


def test_redact_value_replaces_literal() -> None:
    patterns = build_redaction_patterns(SECRET)
    text = f"hook secret is {SECRET.decode('latin-1')} end"
    result = redact_value(text, patterns)
    assert "[REDACTED]" in result
    assert SECRET.decode("latin-1") not in result


def test_redact_value_replaces_hex() -> None:
    patterns = build_redaction_patterns(SECRET)
    text = f"hex={SECRET.hex()} end"
    result = redact_value(text, patterns)
    assert "[REDACTED]" in result
    assert SECRET.hex() not in result


def test_redact_value_replaces_base64() -> None:
    patterns = build_redaction_patterns(SECRET)
    b64 = base64.b64encode(SECRET).decode()
    text = f"b64={b64} end"
    result = redact_value(text, patterns)
    assert "[REDACTED]" in result
    assert b64 not in result


def test_apply_common_redaction_removes_bearer_token() -> None:
    text = "Authorization: Bearer abc123XYZtoken"
    result = apply_common_redaction(text)
    assert "abc123XYZtoken" not in result
    assert "[REDACTED]" in result


def test_apply_common_redaction_removes_api_key_pattern() -> None:
    text = "api_key=mysupersecretvalue123"
    result = apply_common_redaction(text)
    assert "mysupersecretvalue123" not in result
    assert "[REDACTED]" in result


# --- Project-specific bare secret formats ---

# Realistic but FAKE secrets below (do not use real tokens).
FAKE_DISCORD_TOKEN = (
    "MTAxMjM0NTY3ODkwMTIzNDU2Nzg.GaBcDe.aBcDeFgHiJkLmNoPqRsTuVwXyZ012345"
)
FAKE_ANTHROPIC_KEY = "sk-ant-api03-AbCdEf1234567890GhIjKlMnOpQrStUvWxYz"
FAKE_DB_URL = "postgresql+asyncpg://dbuser:dbpass123@db:5432/discode"


def test_apply_common_redaction_removes_bare_discord_token() -> None:
    text = f"Logged in with token {FAKE_DISCORD_TOKEN} now"
    result = apply_common_redaction(text)
    assert FAKE_DISCORD_TOKEN not in result
    # No segment of the token should leak.
    for segment in FAKE_DISCORD_TOKEN.split("."):
        assert segment not in result
    assert "[REDACTED]" in result


def test_apply_common_redaction_removes_anthropic_key_fully() -> None:
    text = f"using key {FAKE_ANTHROPIC_KEY} for the call"
    result = apply_common_redaction(text)
    assert FAKE_ANTHROPIC_KEY not in result
    # The key body must not leak (regression: sk- rule stopped at first dash).
    assert "ant-api03" not in result
    assert "AbCdEf1234567890GhIjKlMnOpQrStUvWxYz" not in result
    assert "[REDACTED]" in result


def test_apply_common_redaction_removes_db_url_credentials() -> None:
    text = f"DATABASE_URL is {FAKE_DB_URL} connected"
    result = apply_common_redaction(text)
    # The embedded credentials must not leak.
    assert "dbuser:dbpass123" not in result
    assert "dbpass123" not in result
    assert "[REDACTED]" in result


def test_apply_common_redaction_does_not_over_redact_benign_text() -> None:
    benign = (
        "This is a normal sentence about the project. "
        "See https://example.com/path/to/page?ref=1 for docs. "
        "Commit 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b is fixed."
    )
    result = apply_common_redaction(benign)
    assert result == benign
    assert "[REDACTED]" not in result
