"""Security regression tests for build_exec_session env scrubbing + SSRF guard.

These exercise pure logic with a MagicMock adapter and patched get_adapter, so
they run without a DB / docker.

Covers:
- FIX 1: full host env (secrets like DISCORD_TOKEN) must NOT leak to the child.
- FIX 2: base_url override SSRF validation (public https allowed, private/loopback
  + non-http schemes blocked).
- FIX 5: resume path (empty overlay) reconstructs a correct scrubbed env.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from discode.runner.saga_input import (
    _EXEC_TIMEOUT_SECONDS,
    _lease_seconds,
    build_exec_session,
    is_base_url_allowed,
)

# --------------------------------------------------------------------------
# FIX 6 — lease must cover the exec window so it can't be reclaimed mid-turn
# --------------------------------------------------------------------------


def test_lease_covers_exec_window():
    assert _lease_seconds() >= _EXEC_TIMEOUT_SECONDS


def _make_payload():
    payload = MagicMock()
    payload.text = "hello"
    payload.idempotency_key = "k"
    return payload


def _adapter(extra_paths=None, env_allowlist=None):
    fake_adapter = MagicMock()
    fake_adapter.extra_paths.return_value = extra_paths or []
    fake_adapter.env_allowlist.return_value = env_allowlist or []
    fake_adapter.exec_cmd.return_value = ["claude", "--print", "hello"]
    fake_adapter.exec_uses_pty.return_value = False
    return fake_adapter


# --------------------------------------------------------------------------
# FIX 1 — secrets must not leak through raw os.environ
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secret_env_not_leaked_to_child(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("DISCORD_TOKEN", "super-secret-token")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host/db")
    monkeypatch.setenv("REDIS_URL", "redis://host:6379")

    store_entry = {
        "tool": "claude",
        "cwd": str(tmp_path),
        "env": {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
        "tool_resume_token": None,
    }
    fake_tool_def = MagicMock()
    fake_tool_def.model_flag = None
    with patch(
        "discode.runner.saga_input.get_adapter",
        new=AsyncMock(return_value=(_adapter(), fake_tool_def)),
    ):
        sess = await build_exec_session(store_entry, _make_payload(), MagicMock())

    assert "DISCORD_TOKEN" not in sess._env
    assert "DATABASE_URL" not in sess._env
    assert "REDIS_URL" not in sess._env
    # Allowlisted base vars survive.
    assert "PATH" in sess._env
    assert sess._env.get("LANG") == "en_US.UTF-8"


@pytest.mark.asyncio
async def test_empty_overlay_reconstructs_scrubbed_env(monkeypatch, tmp_path: Path):
    """FIX 1/FIX 5: resume/recover store env={}, so build must reconstruct a
    scrubbed base from os.environ rather than spreading raw os.environ."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("DISCORD_TOKEN", "super-secret-token")
    monkeypatch.setenv("MY_TOOL_KEY", "tool-secret")

    store_entry = {
        "tool": "claude",
        "cwd": str(tmp_path),
        "env": {},  # resume/recover case
        "tool_resume_token": None,
    }
    fake_tool_def = MagicMock()
    fake_tool_def.model_flag = None
    with patch(
        "discode.runner.saga_input.get_adapter",
        new=AsyncMock(return_value=(_adapter(env_allowlist=["MY_TOOL_KEY"]), fake_tool_def)),
    ):
        sess = await build_exec_session(store_entry, _make_payload(), MagicMock())

    assert "DISCORD_TOKEN" not in sess._env
    # adapter allowlist var passes through
    assert sess._env.get("MY_TOOL_KEY") == "tool-secret"
    assert sess._env.get("LANG") == "en_US.UTF-8"
    assert "PATH" in sess._env


# --------------------------------------------------------------------------
# FIX 2 — base_url SSRF validation
# --------------------------------------------------------------------------


def test_is_base_url_allowed_public_https():
    assert is_base_url_allowed("https://api.anthropic.com") is True


@pytest.mark.parametrize(
    "url",
    [
        "ftp://api.anthropic.com",  # bad scheme
        "file:///etc/passwd",  # bad scheme
        "http://127.0.0.1:8080",  # loopback
        "http://localhost:8080",  # loopback name
        "http://10.0.0.5",  # private
        "http://172.16.0.1",  # private
        "http://192.168.1.1",  # private
        "http://169.254.169.254/latest/meta-data/",  # link-local metadata
        "http://[::1]:8080",  # ipv6 loopback
        "http://0.0.0.0",  # unspecified
        "not-a-url",
    ],
)
def test_is_base_url_allowed_blocks(url):
    assert is_base_url_allowed(url) is False


@pytest.mark.asyncio
async def test_base_url_override_blocked_not_applied(monkeypatch, tmp_path: Path):
    """A private base_url override must NOT be written into the child env."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    store_entry = {
        "tool": "claude",
        "cwd": str(tmp_path),
        "env": {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        "tool_resume_token": None,
    }
    fake_tool_def = MagicMock()
    fake_tool_def.model_flag = None
    fake_tool_def.api_key_env = "ANTHROPIC_API_KEY"
    fake_tool_def.base_url_env = "ANTHROPIC_BASE_URL"

    # Patch the override loader path by injecting via SessionConfig mock is hard
    # here; instead patch the internal override dict by patching get_adapter and
    # the select query. Simpler: drive through is_base_url_allowed unit test +
    # this asserts the apply branch. We simulate overrides via monkeypatching.
    with patch(
        "discode.runner.saga_input.get_adapter",
        new=AsyncMock(return_value=(_adapter(), fake_tool_def)),
    ), patch(
        "discode.runner.saga_input._load_overrides",
        new=AsyncMock(return_value={"base_url": "http://169.254.169.254"}),
    ):
        sess = await build_exec_session(store_entry, _make_payload(), MagicMock())

    assert "ANTHROPIC_BASE_URL" not in sess._env


@pytest.mark.asyncio
async def test_base_url_override_public_applied(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    store_entry = {
        "tool": "claude",
        "cwd": str(tmp_path),
        "env": {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        "tool_resume_token": None,
    }
    fake_tool_def = MagicMock()
    fake_tool_def.model_flag = None
    fake_tool_def.api_key_env = "ANTHROPIC_API_KEY"
    fake_tool_def.base_url_env = "ANTHROPIC_BASE_URL"

    with patch(
        "discode.runner.saga_input.get_adapter",
        new=AsyncMock(return_value=(_adapter(), fake_tool_def)),
    ), patch(
        "discode.runner.saga_input._load_overrides",
        new=AsyncMock(return_value={"base_url": "https://api.anthropic.com"}),
    ):
        sess = await build_exec_session(store_entry, _make_payload(), MagicMock())

    assert sess._env.get("ANTHROPIC_BASE_URL") == "https://api.anthropic.com"
