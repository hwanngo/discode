from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from discode.tools.errors import BadToolDefinition
from discode.tools.generic import GenericAdapter


def _row(**overrides):
    base = dict(
        name="t",
        display_name="T",
        argv_prefix=["t"],
        argv_resume_tokens=["--r", "{token}"],
        argv_suffix=["{prompt}"],
        uses_pty=False,
        resume_token_source="none",
        resume_token_pattern=None,
        reply_extractor="stdout_raw",
        reply_json_field=None,
        extra_paths=[],
        env_passthrough=[],
        enabled=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_cold_argv_substitutes_prompt():
    a = GenericAdapter(_row())
    assert a.exec_cmd("hi", resume_token=None) == ["t", "hi"]


def test_resume_argv_includes_resume_tokens():
    a = GenericAdapter(_row())
    assert a.exec_cmd("hi", resume_token="ABC") == ["t", "--r", "ABC", "hi"]


def test_argv_suffix_with_embedded_prompt_placeholder():
    a = GenericAdapter(_row(argv_suffix=["--message={prompt}"]))
    assert a.exec_cmd("hi", resume_token=None) == ["t", "--message=hi"]


def test_argv_suffix_missing_prompt_raises_bad_definition():
    with pytest.raises(BadToolDefinition):
        GenericAdapter(_row(argv_suffix=["--no-prompt-here"]))


def test_argv_resume_tokens_no_token_placeholder_allowed_for_sentinel():
    a = GenericAdapter(
        _row(
            argv_resume_tokens=["--continue"],
            resume_token_source="sentinel",
            resume_token_pattern="opencode",
        )
    )
    assert a.exec_cmd("hi", resume_token="opencode") == ["t", "--continue", "hi"]


def test_argv_resume_tokens_no_token_placeholder_rejected_for_non_sentinel():
    with pytest.raises(BadToolDefinition):
        GenericAdapter(_row(argv_resume_tokens=["--continue"]))  # default source=none, no {token}


def test_uses_pty_reflects_row():
    assert GenericAdapter(_row(uses_pty=True)).exec_uses_pty() is True
    assert GenericAdapter(_row(uses_pty=False)).exec_uses_pty() is False


def test_extra_paths_passthrough():
    a = GenericAdapter(_row(extra_paths=["~/.local/bin", "/opt/x"]))
    assert a.extra_paths() == ["~/.local/bin", "/opt/x"]


def test_env_allowlist_passthrough():
    a = GenericAdapter(_row(env_passthrough=["FOO", "BAR"]))
    assert a.env_allowlist() == ["FOO", "BAR"]


def test_validate_rejects_invalid_resume_source():
    with pytest.raises(BadToolDefinition):
        GenericAdapter(_row(resume_token_source="bogus"))


def test_validate_rejects_invalid_reply_extractor():
    with pytest.raises(BadToolDefinition):
        GenericAdapter(_row(reply_extractor="bogus"))


def test_validate_rejects_empty_argv_prefix():
    with pytest.raises(BadToolDefinition):
        GenericAdapter(_row(argv_prefix=[]))


def test_validate_requires_pattern_for_regex_source():
    with pytest.raises(BadToolDefinition):
        GenericAdapter(_row(resume_token_source="stdout_regex", resume_token_pattern=None))


def test_validate_requires_json_field_when_extractor_is_json_field():
    with pytest.raises(BadToolDefinition):
        GenericAdapter(_row(reply_extractor="json_field", reply_json_field=None))


# === Resume-token sources ===


def test_resume_none_returns_none():
    a = GenericAdapter(_row(resume_token_source="none"))
    assert a.parse_resume_token("anything", "anything") is None


def test_resume_sentinel_returns_pattern_value():
    a = GenericAdapter(
        _row(
            argv_resume_tokens=["--continue"],
            resume_token_source="sentinel",
            resume_token_pattern="opencode",
        )
    )
    assert a.parse_resume_token("", "") == "opencode"


def test_resume_stderr_regex_captures_group():
    a = GenericAdapter(
        _row(
            resume_token_source="stderr_regex",
            resume_token_pattern=r"session id:\s*([0-9a-zA-Z-]+)",
        )
    )
    assert a.parse_resume_token("", "session id: abc-123\n") == "abc-123"


def test_resume_stdout_regex_falls_back_to_stdout():
    a = GenericAdapter(
        _row(
            resume_token_source="stdout_regex",
            resume_token_pattern=r"id=(\w+)",
        )
    )
    assert a.parse_resume_token("id=xyz", "") == "xyz"
    assert a.parse_resume_token("", "id=xyz") is None


def test_resume_json_field_extracts_key():
    a = GenericAdapter(
        _row(
            resume_token_source="json_field",
            resume_token_pattern="session_id",
        )
    )
    assert a.parse_resume_token('{"session_id":"abc"}', "") == "abc"


def test_resume_json_field_returns_none_on_garbage():
    a = GenericAdapter(
        _row(
            resume_token_source="json_field",
            resume_token_pattern="session_id",
        )
    )
    assert a.parse_resume_token("not json", "") is None


def test_resume_json_field_returns_none_when_key_missing():
    a = GenericAdapter(
        _row(
            resume_token_source="json_field",
            resume_token_pattern="session_id",
        )
    )
    assert a.parse_resume_token('{"other":"x"}', "") is None


# === caller_uuid: discode mints the session id instead of scraping it ===


def test_caller_uuid_is_a_valid_resume_source():
    a = GenericAdapter(
        _row(argv_resume_tokens=["--session-id", "{token}"], resume_token_source="caller_uuid")
    )
    assert a.name == "t"


def test_caller_uuid_does_not_require_a_resume_token_pattern():
    a = GenericAdapter(
        _row(
            argv_resume_tokens=["--session-id", "{token}"],
            resume_token_source="caller_uuid",
            resume_token_pattern=None,
        )
    )
    assert a.parse_resume_token("", "") is None


def test_caller_uuid_requires_token_placeholder_in_resume_tokens():
    with pytest.raises(BadToolDefinition):
        GenericAdapter(_row(argv_resume_tokens=["--session-id"], resume_token_source="caller_uuid"))


def test_caller_uuid_never_overwrites_the_minted_token():
    """The runner persists parse_resume_token()'s result; returning anything
    non-None here would clobber the id discode assigned at session creation."""
    a = GenericAdapter(
        _row(argv_resume_tokens=["--session-id", "{token}"], resume_token_source="caller_uuid")
    )
    assert a.parse_resume_token('{"session_id":"other"}', "session id: other") is None


def test_mint_initial_token_returns_none_for_scraped_sources():
    for src, pat in [
        ("none", None),
        ("sentinel", "opencode"),
        ("stdout_regex", r"id=(\w+)"),
        ("stderr_regex", r"id=(\w+)"),
        ("json_field", "session_id"),
    ]:
        a = GenericAdapter(_row(resume_token_source=src, resume_token_pattern=pat))
        assert a.mint_initial_token() is None, src


def test_mint_initial_token_returns_a_uuid_for_caller_uuid():
    a = GenericAdapter(
        _row(argv_resume_tokens=["--session-id", "{token}"], resume_token_source="caller_uuid")
    )
    token = a.mint_initial_token()
    assert token is not None
    assert uuid.UUID(token)  # raises if not a well-formed UUID


def test_mint_initial_token_is_unique_per_call():
    """Two sessions on the same tool must not share a session id, or the
    second would resume into the first one's conversation."""
    a = GenericAdapter(
        _row(argv_resume_tokens=["--session-id", "{token}"], resume_token_source="caller_uuid")
    )
    assert a.mint_initial_token() != a.mint_initial_token()


def test_caller_uuid_passes_token_on_the_very_first_turn():
    """Unlike scraped sources, the token exists before turn 1, so argv must
    carry it immediately — that is what makes turn 1 and turn 2 one session."""
    a = GenericAdapter(
        _row(argv_resume_tokens=["--session-id", "{token}"], resume_token_source="caller_uuid")
    )
    assert a.exec_cmd("hi", resume_token="UUID-1") == ["t", "--session-id", "UUID-1", "hi"]


# === Reply extractors ===


def test_reply_stdout_raw():
    a = GenericAdapter(_row(reply_extractor="stdout_raw"))
    assert a.extract_reply("hello", "") == "hello"


def test_reply_stdout_strip_ansi_strips_csi():
    a = GenericAdapter(_row(reply_extractor="stdout_strip_ansi"))
    assert a.extract_reply("\x1b[31mred\x1b[0m text", "") == "red text"


def test_reply_json_field_extracts():
    a = GenericAdapter(_row(reply_extractor="json_field", reply_json_field="result"))
    assert a.extract_reply('{"result":"hi","cost":0.01}', "") == "hi"


def test_reply_json_field_falls_back_to_stdout_on_garbage():
    a = GenericAdapter(_row(reply_extractor="json_field", reply_json_field="result"))
    assert a.extract_reply("not json", "") == "not json"


def test_reply_json_field_falls_back_when_key_missing():
    a = GenericAdapter(_row(reply_extractor="json_field", reply_json_field="result"))
    assert a.extract_reply('{"other":"x"}', "") == '{"other":"x"}'


def test_reply_stderr_fallback_on_empty_stdout():
    a = GenericAdapter(_row(reply_extractor="stderr_fallback"))
    assert a.extract_reply("", "stderr msg") == "stderr msg"


def test_reply_stderr_fallback_prefers_stdout_when_present():
    a = GenericAdapter(_row(reply_extractor="stderr_fallback"))
    assert a.extract_reply("stdout msg", "stderr msg") == "stdout msg"
