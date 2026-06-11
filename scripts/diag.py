#!/usr/bin/env python3
"""Standalone diagnostic for the discode runner pipeline.

Run with: uv run python scripts/diag.py

Verifies — without touching Discord — that:
1. Required settings load (DISCORD_TOKEN, RUNNER_CONTROL_BIND, RUNNER_CONTROL_AUTH).
2. The claude / codex / opencode CLIs are reachable on PATH.
3. ExecSession can spawn a plain-pipe and PTY subprocess.
4. `claude --print` round-trips end-to-end if the binary is installed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys


def _print_section(name: str) -> None:
    print(f"\n=== {name} ===")


async def _check_settings() -> None:
    _print_section("Settings")
    from discode.config import settings

    print(f"RUNNER_CONTROL_BIND    = {settings.RUNNER_CONTROL_BIND!r}")
    print(f"RUNNER_CONTROL_AUTH set = {bool(settings.RUNNER_CONTROL_AUTH)}")


async def _check_binaries() -> None:
    _print_section("Binary discovery (PATH)")
    print(f"PATH = {os.environ.get('PATH', '')}")
    for tool in ("claude", "codex", "opencode"):
        path = shutil.which(tool)
        print(f"{tool:10s} -> {path}")


async def _check_exec_session() -> None:
    _print_section("ExecSession smoke")
    from discode.tools.exec_session import ExecSession

    sess = ExecSession(
        argv=[sys.executable, "-c", "print('exec_session ok')"],
        cwd="/tmp",
        env={"PATH": os.environ.get("PATH", "")},
        use_pty=False,
    )
    try:
        result = await sess.run(timeout=10.0)
        print(f"plain pipe: exit={result.exit_code} text={result.text!r}")
    except Exception as exc:
        print(f"plain pipe FAILED: {exc!r}")

    sess_pty = ExecSession(
        argv=[sys.executable, "-c", "print('pty ok')"],
        cwd="/tmp",
        env={"PATH": os.environ.get("PATH", "")},
        use_pty=True,
    )
    try:
        result = await sess_pty.run(timeout=10.0)
        print(f"pty mode: exit={result.exit_code} text={result.text!r}")
    except Exception as exc:
        print(f"pty mode FAILED: {exc!r}")


async def _check_claude_print() -> None:
    _print_section("Claude --print smoke (only if `claude` is on PATH)")
    if not shutil.which("claude"):
        print("skip: claude not on PATH")
        return
    from discode.config import settings
    from discode.db.engine import make_engine, make_session_factory
    from discode.tools.exec_session import ExecSession
    from discode.tools.registry import get_adapter

    engine = make_engine(settings.database_url)
    try:
        factory = make_session_factory(engine)
        async with factory() as session:
            adapter, _tool_def = await get_adapter(session, "claude")
    finally:
        await engine.dispose()

    argv = adapter.exec_cmd("Reply with the single word: pong", resume_token=None)
    print(f"argv = {argv}")
    sess = ExecSession(
        argv=argv,
        cwd="/tmp",
        env={**os.environ},
        use_pty=adapter.exec_uses_pty(),
    )
    try:
        result = await sess.run(timeout=60.0)
        reply = adapter.extract_reply(result.text, result.stderr)
        print(f"claude exit={result.exit_code}")
        print(f"claude reply[:200]={reply[:200]!r}")
    except Exception as exc:
        print(f"claude FAILED: {exc!r}")


async def main() -> None:
    print("Discode pipeline diagnostic")
    await _check_settings()
    await _check_binaries()
    await _check_exec_session()
    await _check_claude_print()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
