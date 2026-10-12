"""Shared logging setup (audit 2026-10-12)."""

from __future__ import annotations

import logging

import pytest

from discode.config import Settings
from discode.log import ExtraFormatter


def test_extra_fields_are_rendered():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "repaired session", None, None)
    record.session_id = "abc"
    line = ExtraFormatter("%(message)s").format(record)
    assert line == "repaired session session_id=abc"


def test_plain_record_unchanged():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("w",), None)
    assert ExtraFormatter("%(message)s").format(record) == "hello w"


@pytest.mark.parametrize(("raw", "expected"), [("info", "INFO"), (" debug ", "DEBUG")])
def test_log_level_normalized(monkeypatch, raw, expected):
    monkeypatch.setenv("LOG_LEVEL", raw)
    assert Settings().LOG_LEVEL == expected


def test_log_level_rejects_garbage(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "loud")
    with pytest.raises(ValueError):
        Settings()
