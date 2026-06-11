from __future__ import annotations

from discode.runner.session_store import build_session_store_entry


def test_entry_carries_all_required_fields() -> None:
    entry = build_session_store_entry(
        tool="opencode",
        cwd="/tmp",
        env={"PATH": "/x"},
        tool_resume_token="rt",
        guild_id="g",
        thread_id="t",
        host_id="h",
        status="running",
    )
    assert entry["tool"] == "opencode"
    assert entry["cwd"] == "/tmp"
    assert entry["env"] == {"PATH": "/x"}
    assert entry["tool_resume_token"] == "rt"
    assert entry["guild_id"] == "g"
    assert entry["thread_id"] == "t"
    assert entry["host_id"] == "h"
    assert entry["status"] == "running"


def test_channel_id_defaults_empty() -> None:
    entry = build_session_store_entry(
        tool="codex",
        cwd="/tmp",
        env={},
        tool_resume_token=None,
        guild_id="g",
        thread_id="t",
        host_id="h",
        status="running",
    )
    assert entry["channel_id"] == ""
