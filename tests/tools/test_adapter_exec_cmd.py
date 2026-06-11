from __future__ import annotations

from types import SimpleNamespace

from discode.tools.base import ToolAdapter
from discode.tools.generic import GenericAdapter


class _Stub(ToolAdapter):
    @property
    def name(self) -> str:
        return "stub"

    def exec_cmd(self, prompt: str, *, resume_token: str | None) -> list[str]:
        return ["stub", prompt]


def test_default_exec_uses_pty_is_false() -> None:
    assert _Stub().exec_uses_pty() is False


def test_default_parse_resume_token_returns_none() -> None:
    assert _Stub().parse_resume_token("anything", "stderr-too") is None


def test_default_extract_reply_returns_stdout() -> None:
    assert _Stub().extract_reply("hello", "ignored") == "hello"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _claude() -> GenericAdapter:
    return GenericAdapter(
        SimpleNamespace(
            name="claude",
            display_name="Claude Code",
            argv_prefix=[
                "claude",
                "--permission-mode",
                "bypassPermissions",
                "--print",
                "--output-format",
                "json",
            ],
            argv_resume_tokens=["--resume", "{token}"],
            argv_suffix=["{prompt}"],
            uses_pty=False,
            resume_token_source="json_field",
            resume_token_pattern="session_id",
            reply_extractor="json_field",
            reply_json_field="result",
            extra_paths=[],
            env_passthrough=[],
            enabled=True,
        )
    )


def _codex() -> GenericAdapter:
    return GenericAdapter(
        SimpleNamespace(
            name="codex",
            display_name="OpenAI Codex",
            argv_prefix=["codex", "exec"],
            argv_resume_tokens=["--resume", "{token}"],
            argv_suffix=["{prompt}"],
            uses_pty=True,
            resume_token_source="stderr_regex",
            resume_token_pattern=r"session id:\s*([0-9a-zA-Z-]+)",
            reply_extractor="stdout_strip_ansi",
            reply_json_field=None,
            extra_paths=[],
            env_passthrough=[],
            enabled=True,
        )
    )


def _opencode() -> GenericAdapter:
    return GenericAdapter(
        SimpleNamespace(
            name="opencode",
            display_name="OpenCode",
            argv_prefix=["opencode", "run"],
            argv_resume_tokens=["--continue"],
            argv_suffix=["{prompt}"],
            uses_pty=True,
            resume_token_source="sentinel",
            resume_token_pattern="opencode",
            reply_extractor="stdout_strip_ansi",
            reply_json_field=None,
            extra_paths=[],
            env_passthrough=[],
            enabled=True,
        )
    )


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------


def test_claude_exec_cmd_no_resume() -> None:
    cmd = _claude().exec_cmd("hello", resume_token=None)
    assert cmd == [
        "claude",
        "--permission-mode",
        "bypassPermissions",
        "--print",
        "--output-format",
        "json",
        "hello",
    ]


def test_claude_exec_cmd_with_resume() -> None:
    cmd = _claude().exec_cmd("hello", resume_token="abc-123")
    assert cmd == [
        "claude",
        "--permission-mode",
        "bypassPermissions",
        "--print",
        "--output-format",
        "json",
        "--resume",
        "abc-123",
        "hello",
    ]


def test_claude_no_pty() -> None:
    assert _claude().exec_uses_pty() is False


def test_claude_parse_resume_token_from_json() -> None:
    stdout = '{"result": "hi", "session_id": "sess-xyz"}'
    assert _claude().parse_resume_token(stdout, "") == "sess-xyz"


def test_claude_parse_resume_token_missing_session_id() -> None:
    stdout = '{"result": "hi"}'
    assert _claude().parse_resume_token(stdout, "") is None


def test_claude_parse_resume_token_non_json_returns_none() -> None:
    assert _claude().parse_resume_token("plain text", "") is None


def test_claude_extract_reply_from_json_result() -> None:
    stdout = '{"result": "Hey! What\'s up?", "session_id": "s1"}'
    assert _claude().extract_reply(stdout, "") == "Hey! What's up?"


def test_claude_extract_reply_falls_back_to_stdout_on_invalid_json() -> None:
    stdout = "not json at all"
    assert _claude().extract_reply(stdout, "") == "not json at all"


def test_claude_extract_reply_falls_back_to_stdout_when_no_text_field() -> None:
    stdout = '{"session_id": "s1", "type": "result"}'
    assert _claude().extract_reply(stdout, "") == stdout


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------


def test_codex_exec_cmd_no_resume() -> None:
    cmd = _codex().exec_cmd("hello world", resume_token=None)
    assert cmd == ["codex", "exec", "hello world"]


def test_codex_exec_cmd_with_resume() -> None:
    cmd = _codex().exec_cmd("hello", resume_token="sess-1")
    assert cmd == ["codex", "exec", "--resume", "sess-1", "hello"]


def test_codex_uses_pty() -> None:
    assert _codex().exec_uses_pty() is True


def test_codex_parse_resume_token_from_stderr() -> None:
    stderr = "[2026-05-02T10:00:00Z] session id: a1b2c3d4-e5f6\n"
    token = _codex().parse_resume_token("", stderr)
    assert token == "a1b2c3d4-e5f6"


def test_codex_parse_resume_token_absent() -> None:
    assert _codex().parse_resume_token("nothing", "") is None


# ---------------------------------------------------------------------------
# OpenCode
# ---------------------------------------------------------------------------


def test_opencode_exec_cmd_no_resume() -> None:
    cmd = _opencode().exec_cmd("draft a todo", resume_token=None)
    assert cmd == ["opencode", "run", "draft a todo"]


def test_opencode_exec_cmd_with_resume() -> None:
    cmd = _opencode().exec_cmd("more", resume_token="any-truthy")
    assert cmd == ["opencode", "run", "--continue", "more"]


def test_opencode_uses_pty() -> None:
    assert _opencode().exec_uses_pty() is True


def test_opencode_parse_resume_token_returns_sentinel() -> None:
    # opencode doesn't expose a CLI session id; we just signal "session exists, use --continue"
    assert _opencode().parse_resume_token("anything", "anything") == "opencode"
