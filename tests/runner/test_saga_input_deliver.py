from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from discode.runner.saga_input import InputJobPayload, _deliver, _strip_ansi
from discode.tools.exec_session import ExecFailed, ExecTimeout


def test_strip_ansi():
    assert _strip_ansi("\x1b[0mhello\x1b[0m") == "hello"
    assert _strip_ansi("") == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, ExecTimeout(300), ExecFailed(2, "private", "private")])
async def test_execution_produces_result_before_any_network_delivery(monkeypatch, failure):
    from discode.runner import saga_input
    from discode.security import execution_policy

    monkeypatch.setattr(execution_policy, "ensure_execution_allowed", lambda: None)
    run = AsyncMock(return_value=SimpleNamespace(text="answer", stderr="", exit_code=0))
    run.side_effect = failure
    monkeypatch.setattr(
        saga_input, "build_exec_session", AsyncMock(return_value=SimpleNamespace(run=run))
    )
    adapter = SimpleNamespace(
        extract_reply=lambda text, err: text, parse_resume_token=lambda text, err: "token"
    )
    monkeypatch.setattr(saga_input, "get_adapter", AsyncMock(return_value=(adapter, None)))
    network = AsyncMock()
    monkeypatch.setattr(saga_input, "get_reply_client", network)

    @asynccontextmanager
    async def factory():
        yield None

    result, token = await _deliver(
        InputJobPayload("s", "g", "t", "h", "input", "key"),
        {"tool": "test", "lifecycle_version": 0},
        factory,
    )
    network.assert_not_called()
    if failure is None:
        assert (result, token) == ("answer", "token")
    else:
        assert result.startswith("⚠️")
        assert "private" not in result
        assert token is None


@pytest.mark.asyncio
async def test_leading_dash_prompt_gets_actionable_message(monkeypatch):
    from discode.runner import saga_input
    from discode.security import execution_policy
    from discode.tools.generic import AmbiguousPrompt

    monkeypatch.setattr(execution_policy, "ensure_execution_allowed", lambda: None)
    monkeypatch.setattr(
        saga_input, "build_exec_session", AsyncMock(side_effect=AmbiguousPrompt("x"))
    )

    @asynccontextmanager
    async def factory():
        yield None

    result, token = await _deliver(
        InputJobPayload("s", "g", "t", "h", "- fix it", "key"),
        {"tool": "test", "lifecycle_version": 0},
        factory,
    )
    assert "start with `-`" in result
    assert token is None


@pytest.mark.asyncio
async def test_oversized_reply_is_truncated_below_endpoint_limit(monkeypatch):
    """Review 2026-10-12: replies must fit the control API's 1,000,000-char body limit."""
    from discode.runner import saga_input
    from discode.security import execution_policy

    monkeypatch.setattr(execution_policy, "ensure_execution_allowed", lambda: None)
    big = "x" * 1_200_000
    run = AsyncMock(return_value=SimpleNamespace(text=big, stderr="", exit_code=0))
    monkeypatch.setattr(
        saga_input, "build_exec_session", AsyncMock(return_value=SimpleNamespace(run=run))
    )
    adapter = SimpleNamespace(extract_reply=lambda t, e: t, parse_resume_token=lambda t, e: None)
    monkeypatch.setattr(saga_input, "get_adapter", AsyncMock(return_value=(adapter, None)))

    @asynccontextmanager
    async def factory():
        yield None

    result, _ = await _deliver(
        InputJobPayload("s", "g", "t", "h", "input", "key"),
        {"tool": "test", "lifecycle_version": 0},
        factory,
    )
    assert len(result) < 1_000_000
    assert "reply truncated" in result
