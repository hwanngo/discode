"""Regression matrix for GenericAdapter against committed sample outputs.

Each scenario verifies the seeded GenericAdapter produces the documented
behavior for one tool. Seed configs are loaded from
``alembic/seeds/tool_definitions.json`` — the same source the migration
uses, so this suite catches drift between the seed and the adapter.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from discode.tools.generic import GenericAdapter

FIXTURES = Path(__file__).parent.parent / "fixtures" / "tool_outputs"
_SEEDS_PATH = Path(__file__).parent.parent.parent / "alembic" / "seeds" / "tool_definitions.json"


def _read(name: str) -> str:
    p = FIXTURES / name
    return p.read_text() if p.exists() else ""


def _load_seeds() -> dict[str, dict]:
    with _SEEDS_PATH.open("r", encoding="utf-8") as f:
        return {row["name"]: row for row in json.load(f)}


SEED_CONFIGS = _load_seeds()


def _generic(name: str) -> GenericAdapter:
    return GenericAdapter(SimpleNamespace(**SEED_CONFIGS[name]))


SCENARIOS = [
    # (name, stdout_t, stderr_t, exp_token, exp_reply, stdout_n, stderr_n, exp_no_token)
    (
        "claude",
        _read("claude_with_token.txt"),
        "",
        "sess-abc-123",
        "hello, world",
        _read("claude_no_token.txt"),
        "",
        None,
    ),
    (
        "codex",
        _read("codex_with_token.txt"),
        _read("codex_with_token.stderr.txt"),
        "cdx-789-xyz",
        "hello from codex\n",
        _read("codex_no_token.txt"),
        _read("codex_no_token.stderr.txt"),
        None,
    ),
    (
        "opencode",
        _read("opencode_with_token.txt"),
        "",
        "opencode",
        "hello from opencode\n",
        _read("opencode_no_token.txt"),
        "",
        "opencode",
    ),
]

_PARAMS = "name,stdout_t,stderr_t,exp_token,exp_reply,stdout_n,stderr_n,exp_no_token"


@pytest.mark.parametrize(_PARAMS, SCENARIOS)
def test_resume_token_with(
    name, stdout_t, stderr_t, exp_token, exp_reply, stdout_n, stderr_n, exp_no_token
):
    assert _generic(name).parse_resume_token(stdout_t, stderr_t) == exp_token


@pytest.mark.parametrize(_PARAMS, SCENARIOS)
def test_resume_token_without(
    name, stdout_t, stderr_t, exp_token, exp_reply, stdout_n, stderr_n, exp_no_token
):
    assert _generic(name).parse_resume_token(stdout_n, stderr_n) == exp_no_token


@pytest.mark.parametrize(_PARAMS, SCENARIOS)
def test_extract_reply(
    name, stdout_t, stderr_t, exp_token, exp_reply, stdout_n, stderr_n, exp_no_token
):
    assert _generic(name).extract_reply(stdout_t, stderr_t) == exp_reply


@pytest.mark.parametrize(_PARAMS, SCENARIOS)
def test_argv_cold(
    name, stdout_t, stderr_t, exp_token, exp_reply, stdout_n, stderr_n, exp_no_token
):
    argv = _generic(name).exec_cmd("hi", resume_token=None)
    assert argv[0] == name
    assert argv[-1] == "hi"


@pytest.mark.parametrize(_PARAMS, SCENARIOS)
def test_argv_resume(
    name, stdout_t, stderr_t, exp_token, exp_reply, stdout_n, stderr_n, exp_no_token
):
    argv = _generic(name).exec_cmd("hi", resume_token="TOK")
    assert "TOK" in argv or "--continue" in argv  # opencode uses sentinel-only
