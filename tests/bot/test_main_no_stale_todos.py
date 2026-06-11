"""Lightweight regression guards: future refactors must not reintroduce
stale TODOs or dead NotImplementedError safety nets in bot/main.py.
"""

from pathlib import Path


def test_no_stale_implementation_todos():
    src = Path("src/discode/bot/main.py").read_text()
    assert "TODO: implement" not in src, (
        "stale 'TODO: implement' comment left in bot/main.py — "
        "remove the comment when the function is implemented"
    )


def test_no_dead_notimpl_fallbacks():
    src = Path("src/discode/bot/main.py").read_text()
    assert "ImportError, AttributeError, NotImplementedError" not in src, (
        "dead NotImplementedError except-fallback left in bot/main.py — "
        "delete it once the underlying saga is implemented"
    )


def test_session_service_no_dead_run_input_saga_stub():
    src = Path("src/discode/bot/session_service.py").read_text()
    assert 'NotImplementedError("run_input_saga' not in src, (
        "dead run_input_saga stub left in session_service.py — "
        "the live saga lives at bot/sagas/input.py"
    )
