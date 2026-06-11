from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from discode.runner.saga_input import InputJobPayload, _deliver, _strip_ansi


def _make_fake_adapter(tool: str = "claude") -> MagicMock:
    """Return a MagicMock that looks like a ToolAdapter for deliver tests."""
    adapter = MagicMock()
    adapter.name = tool
    adapter.extract_reply.side_effect = lambda stdout, stderr: stdout
    adapter.parse_resume_token.side_effect = lambda stdout, stderr: None
    return adapter


def _make_fake_tool_def(tool: str = "claude") -> MagicMock:
    """Return a MagicMock that looks like a ToolDefinition row."""
    td = MagicMock()
    td.name = tool
    td.model_flag = None
    td.api_key_env = None
    td.base_url_env = None
    td.argv_prefix = [tool]
    return td


def test_strip_ansi_removes_csi_sequences() -> None:
    sample = "\x1b[0m\n> Sisyphus · kimi-k2\n\x1b[0m\nYo yo!\x1b[0m\n"
    cleaned = _strip_ansi(sample)
    assert "\x1b" not in cleaned
    assert "[0m" not in cleaned
    assert "Yo yo!" in cleaned
    assert "Sisyphus" in cleaned


def test_strip_ansi_handles_empty_string() -> None:
    assert _strip_ansi("") == ""


def _make_db_factory_supporting_token_update():
    """db_factory() returns an async session that supports `async with db.begin()`
    and `await db.execute(...)`. Returns (factory, session_mock) so callers can
    assert on session.execute calls."""
    sess = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=sess)
    cm.__aexit__ = AsyncMock(return_value=None)

    txn = MagicMock()
    txn.__aenter__ = AsyncMock(return_value=txn)
    txn.__aexit__ = AsyncMock(return_value=None)
    sess.begin = MagicMock(return_value=txn)

    factory = MagicMock(return_value=cm)
    return factory, sess


@pytest.mark.asyncio
async def test_deliver_runs_exec_and_posts_reply() -> None:
    payload = InputJobPayload(
        session_id="sid-1",
        guild_id="g",
        thread_id="t",
        host_id="h",
        text="hi",
        idempotency_key="k1",
    )
    store_entry = {
        "tool": "claude",
        "cwd": "/tmp",
        "env": {},
        "tool_resume_token": None,
        "guild_id": "g",
        "thread_id": "t",
    }

    fake_exec = MagicMock()
    fake_exec.run = AsyncMock(return_value=MagicMock(text="hello-back", stderr="", exit_code=0))
    fake_reply = MagicMock()
    fake_reply.post_reply = AsyncMock(return_value="msg-77")

    factory, _ = _make_db_factory_supporting_token_update()

    with (
        patch(
            "discode.runner.saga_input.build_exec_session", new=AsyncMock(return_value=fake_exec)
        ),
        patch("discode.runner.saga_input.get_reply_client", return_value=fake_reply),
        patch(
            "discode.runner.saga_input.get_adapter",
            new=AsyncMock(
                return_value=(_make_fake_adapter("claude"), _make_fake_tool_def("claude"))
            ),
        ),
    ):
        await _deliver(payload, store_entry, factory)

    fake_exec.run.assert_awaited_once()
    fake_reply.post_reply.assert_awaited_once_with("sid-1", "hello-back", idempotency_key="k1")


@pytest.mark.asyncio
async def test_deliver_persists_new_resume_token() -> None:
    payload = InputJobPayload(
        session_id="sid-1",
        guild_id="g",
        thread_id="t",
        host_id="h",
        text="hi",
        idempotency_key="k1",
    )
    store_entry = {
        "tool": "codex",
        "cwd": "/tmp",
        "env": {},
        "tool_resume_token": None,
        "guild_id": "g",
        "thread_id": "t",
    }

    fake_exec = MagicMock()
    fake_exec.run = AsyncMock(
        return_value=MagicMock(
            text="reply",
            stderr="session id: new-token-1\n",
            exit_code=0,
        )
    )
    fake_reply = MagicMock()
    fake_reply.post_reply = AsyncMock(return_value="m")

    factory, sess = _make_db_factory_supporting_token_update()

    codex_adapter = _make_fake_adapter("codex")
    codex_adapter.parse_resume_token.side_effect = lambda stdout, stderr: (
        "new-token-1" if "new-token-1" in (stderr or "") else None
    )

    with (
        patch(
            "discode.runner.saga_input.build_exec_session", new=AsyncMock(return_value=fake_exec)
        ),
        patch("discode.runner.saga_input.get_reply_client", return_value=fake_reply),
        patch(
            "discode.runner.saga_input.get_adapter",
            new=AsyncMock(return_value=(codex_adapter, _make_fake_tool_def("codex"))),
        ),
    ):
        await _deliver(payload, store_entry, factory)

    assert store_entry["tool_resume_token"] == "new-token-1"
    # Verify the token UPDATE was executed
    sess.execute.assert_awaited()


@pytest.mark.asyncio
async def test_deliver_skips_token_update_when_unchanged() -> None:
    """If the parsed token matches the existing one, no DB write."""
    payload = InputJobPayload(
        session_id="sid-1",
        guild_id="g",
        thread_id="t",
        host_id="h",
        text="hi",
        idempotency_key="k1",
    )
    store_entry = {
        "tool": "opencode",
        "cwd": "/tmp",
        "env": {},
        "tool_resume_token": "opencode",  # opencode adapter always returns "opencode"
        "guild_id": "g",
        "thread_id": "t",
    }

    fake_exec = MagicMock()
    fake_exec.run = AsyncMock(return_value=MagicMock(text="r", stderr="", exit_code=0))
    fake_reply = MagicMock()
    fake_reply.post_reply = AsyncMock(return_value="m")

    factory, sess = _make_db_factory_supporting_token_update()

    opencode_adapter = _make_fake_adapter("opencode")
    opencode_adapter.parse_resume_token.side_effect = lambda stdout, stderr: "opencode"

    with (
        patch(
            "discode.runner.saga_input.build_exec_session", new=AsyncMock(return_value=fake_exec)
        ),
        patch("discode.runner.saga_input.get_reply_client", return_value=fake_reply),
        patch(
            "discode.runner.saga_input.get_adapter",
            new=AsyncMock(return_value=(opencode_adapter, _make_fake_tool_def("opencode"))),
        ),
    ):
        await _deliver(payload, store_entry, factory)

    # No DB writes — token unchanged
    sess.execute.assert_not_awaited()
