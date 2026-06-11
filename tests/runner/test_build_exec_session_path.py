from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from discode.runner.saga_input import (
    _DEFAULT_EXTRA_PATHS,
    _normalize_extra_paths,
    build_exec_session,
)


def test_normalize_expands_tilde(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`~`-prefixed paths are resolved against $HOME."""
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    out = _normalize_extra_paths(["~/.local/bin"])

    assert out == [str(bin_dir)]


def test_normalize_filters_nonexistent_dirs(tmp_path: Path) -> None:
    """Non-existent paths are silently dropped — keeps PATH tight."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    fake_dir = tmp_path / "does-not-exist"

    out = _normalize_extra_paths([str(real_dir), str(fake_dir)])

    assert out == [str(real_dir)]


def test_normalize_dedups_preserving_order(tmp_path: Path) -> None:
    """Duplicate entries appear once; first occurrence wins."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()

    out = _normalize_extra_paths([str(a), str(b), str(a), str(b)])

    assert out == [str(a), str(b)]


def test_normalize_empty_input() -> None:
    """Empty input → empty output, no crash."""
    assert _normalize_extra_paths([]) == []


def test_default_extra_paths_covers_macos_and_linux() -> None:
    """The default allowlist must cover the common install dirs on both OSes
    so build_exec_session works without per-deploy PATH tinkering."""
    expected_subset = {
        # macOS Homebrew
        "/opt/homebrew/bin",
        # Per-user installs (Linux + macOS)
        "~/.local/bin",
        "~/.npm-global/bin",
        "~/.cargo/bin",
        "~/.bun/bin",
        "~/.local/share/pnpm",
        "~/.nix-profile/bin",
        # System-wide
        "/usr/local/bin",
        # Linux distro extras
        "/snap/bin",
    }
    actual = set(_DEFAULT_EXTRA_PATHS)
    missing = expected_subset - actual
    assert not missing, f"missing required PATH entries: {missing}"


def _make_payload():
    payload = MagicMock()
    payload.text = "hello"
    payload.idempotency_key = "k"
    return payload


@pytest.mark.asyncio
async def test_build_exec_session_path_includes_user_local_bin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When ~/.local/bin exists on the host, it appears in the spawned PATH."""
    home = tmp_path / "home"
    user_local_bin = home / ".local" / "bin"
    user_local_bin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    store_entry = {
        "tool": "claude",
        "cwd": str(tmp_path),
        "env": {},
        "tool_resume_token": None,
    }
    fake_adapter = MagicMock()
    fake_adapter.extra_paths.return_value = []
    fake_adapter.exec_cmd.return_value = ["claude", "--print", "hello"]
    fake_adapter.exec_uses_pty.return_value = False
    mock_session = MagicMock()

    fake_tool_def = MagicMock()
    with patch(
        "discode.runner.saga_input.get_adapter",
        new=AsyncMock(return_value=(fake_adapter, fake_tool_def)),
    ):
        sess = await build_exec_session(store_entry, _make_payload(), mock_session)

    assert str(user_local_bin) in sess._env["PATH"].split(":")


@pytest.mark.asyncio
async def test_build_exec_session_path_adapter_extras_come_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Adapter-supplied extra_paths() entries precede the default allowlist
    so tool-specific install hints win over the static list."""
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_opencode = fake_bin / "opencode"
    fake_opencode.write_text("#!/bin/sh\nexit 0\n")
    fake_opencode.chmod(0o755)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")

    store_entry = {
        "tool": "opencode",
        "cwd": str(tmp_path),
        "env": {},
        "tool_resume_token": None,
    }
    fake_adapter = MagicMock()
    fake_adapter.extra_paths.return_value = [str(fake_bin)]
    fake_adapter.exec_cmd.return_value = ["opencode", "run", "--hello"]
    fake_adapter.exec_uses_pty.return_value = False
    mock_session = MagicMock()

    fake_tool_def = MagicMock()
    with patch(
        "discode.runner.saga_input.get_adapter",
        new=AsyncMock(return_value=(fake_adapter, fake_tool_def)),
    ):
        sess = await build_exec_session(store_entry, _make_payload(), mock_session)

    parts = sess._env["PATH"].split(":")
    # Adapter-supplied dir for opencode (the dirname of `opencode` in PATH)
    # must precede /usr/bin (which is in the default allowlist).
    assert str(fake_bin) in parts
    assert parts.index(str(fake_bin)) < parts.index("/usr/bin")
