from __future__ import annotations

import pytest

from discode.security.quotas import (
    MAX_INPUT_BYTES_PER_MESSAGE,
    MAX_INPUT_BYTES_PER_SESSION_PER_MIN,
    MAX_INPUT_MESSAGES_PER_SESSION_PER_MIN,
    MAX_OUTPUT_BYTES_PER_SESSION_PER_MIN,
)


def test_max_input_bytes_per_message_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_INPUT_BYTES_PER_MESSAGE", raising=False)
    assert MAX_INPUT_BYTES_PER_MESSAGE() == 8192


def test_max_input_bytes_per_message_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_INPUT_BYTES_PER_MESSAGE", "4096")
    assert MAX_INPUT_BYTES_PER_MESSAGE() == 4096


def test_max_output_bytes_per_session_per_min_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_OUTPUT_BYTES_PER_SESSION_PER_MIN", raising=False)
    assert MAX_OUTPUT_BYTES_PER_SESSION_PER_MIN() == 262144


def test_max_output_bytes_per_session_per_min_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_OUTPUT_BYTES_PER_SESSION_PER_MIN", "1024")
    assert MAX_OUTPUT_BYTES_PER_SESSION_PER_MIN() == 1024


def test_max_input_messages_per_session_per_min_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_INPUT_MESSAGES_PER_SESSION_PER_MIN", raising=False)
    assert MAX_INPUT_MESSAGES_PER_SESSION_PER_MIN() == 20


def test_max_input_bytes_per_session_per_min_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_INPUT_BYTES_PER_SESSION_PER_MIN", raising=False)
    assert MAX_INPUT_BYTES_PER_SESSION_PER_MIN() == 163840
