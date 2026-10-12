from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from discode.tools.exec_session import ExecFailed, ExecSession, ExecTimeout


@pytest.mark.parametrize("use_pty", [False, True])
async def test_cancelling_turn_reaps_child(tmp_path: Path, use_pty: bool) -> None:
    pidfile = tmp_path / "pid"
    program = f"import os,time;open({str(pidfile)!r},'w').write(str(os.getpid()));time.sleep(60)"
    task = asyncio.create_task(
        ExecSession(
            argv=[sys.executable, "-c", program],
            cwd=str(tmp_path),
            env={},
            use_pty=use_pty,
        ).run()
    )
    async with asyncio.timeout(5):
        while not pidfile.exists() or not pidfile.read_text():
            await asyncio.sleep(0.01)
    pid = int(pidfile.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("use_pty", [False, True])
async def test_deadline_includes_wait_after_output_eof(tmp_path: Path, use_pty: bool) -> None:
    session = ExecSession(
        argv=[sys.executable, "-c", "import os,time;os.close(1);os.close(2);time.sleep(60)"],
        cwd=str(tmp_path),
        env={},
        use_pty=use_pty,
    )
    async with asyncio.timeout(3):
        with pytest.raises(ExecTimeout):
            await session.run(timeout=0.1)


@pytest.mark.parametrize("use_pty", [False, True])
async def test_stderr_is_bounded_in_both_modes(tmp_path: Path, use_pty: bool) -> None:
    session = ExecSession(
        argv=[sys.executable, "-c", "import sys;sys.stderr.write('x'*10_000_000)"],
        cwd=str(tmp_path),
        env={},
        use_pty=use_pty,
        max_output_bytes=1024,
    )
    result = await session.run(timeout=5)
    assert len(result.stderr) < 1100
    assert "[truncated" in result.stderr


async def test_pty_spawn_failure_closes_both_descriptors(monkeypatch: pytest.MonkeyPatch) -> None:
    import discode.tools.exec_session as module

    real_openpty = module.pty.openpty
    allocated = []

    def recording_openpty() -> tuple[int, int]:
        pair = real_openpty()
        allocated.extend(pair)
        return pair

    monkeypatch.setattr(module.pty, "openpty", recording_openpty)
    with pytest.raises(FileNotFoundError):
        await ExecSession(
            argv=["/missing/discode-audit-command"], cwd="/tmp", env={}, use_pty=True
        ).run()
    for fd in allocated:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_failed_exec_exception_does_not_disclose_streams() -> None:
    exc = ExecFailed(1, "private-stderr", "private-stdout")
    assert "private" not in str(exc)
    assert exc.stderr == "private-stderr"
