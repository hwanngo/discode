from __future__ import annotations

import base64

import pytest

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
FAKE_DISCORD_TOKEN = "MTAxMjM0NTY3ODkwMTIzNDU2Nzg.GaBcDe.aBcDeFgHiJkLmNoPqRsTuVwXyZ012345"
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


# --- Audit 2026-10-12 #6: deployment secrets and vendor token formats ---


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        # Fake values are assembled at runtime so secret scanners don't flag the fixtures.
        ("DEPLOYMENT_SECRET_KEY=" + "Zm9vYmFy" * 5 + "MTI=", "Zm9vYmFy"),
        ("RUNNER_CONTROL_AUTH=" + "abcd1234" * 2, "abcd1234"),
        ('{"api_key": "zzzSECRETzzz123"}', "zzzSECRETzzz123"),
        ("export OPENAI_API_KEY='sk-xyz'", "sk-xyz"),
        ("token ghp_" + "a" * 36, "ghp_"),
        ("github_pat_" + "B" * 30, "github_pat_"),
        ("aws " + "AKIA" + "ABCDEFGHIJKLMNOP" + " here", "ABCDEFGHIJKLMNOP"),
        ("key sk-proj-abc_DEF-ghi_jklmnopqrstuvwxyz", "jklmnop"),
        ("AIza" + "C" * 35, "AIza"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----", "MIIabc"),
    ],
)
def test_apply_common_redaction_covers_audit_gaps(text: str, secret: str) -> None:
    result = apply_common_redaction(text)
    assert secret not in result
    assert "[REDACTED]" in result


@pytest.mark.parametrize(
    "code",
    [
        "auth = request.headers.get('x')",
        "the cache_key = compute(x)",
        "primary_key=True",
    ],
)
def test_apply_common_redaction_spares_ordinary_code(code: str) -> None:
    assert apply_common_redaction(code) == code


def test_apply_common_redaction_removes_deployment_secret_values(monkeypatch) -> None:
    from types import SimpleNamespace

    import discode.config
    import discode.security.redaction as redaction

    settings = SimpleNamespace(
        DISCORD_TOKEN="plain-discord-token-value",
        RUNNER_CONTROL_AUTH="control-auth-value-123",
        DEPLOYMENT_SECRET_KEY="fernet-key-one-xxxxxxxx,fernet-key-two-yyyyyyyy",
        DATABASE_URL="postgresql://u:db-password-zzz@h/d",
        REDIS_URL="redis://localhost:6379/0",
    )
    monkeypatch.setattr(discode.config, "get_settings", lambda: settings)
    monkeypatch.setattr(redaction, "_deployment_secret_patterns", None)
    text = (
        "leaked plain-discord-token-value control-auth-value-123 "
        "fernet-key-two-yyyyyyyy db-password-zzz"
    )
    result = apply_common_redaction(text)
    for value in ("plain-discord", "control-auth", "fernet-key-two", "db-password"):
        assert value not in result
