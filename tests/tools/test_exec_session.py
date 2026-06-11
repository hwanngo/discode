from __future__ import annotations

import os
import sys
import time

import pytest

from discode.tools.exec_session import (
    ExecFailed,
    ExecResult,
    ExecSession,
    ExecTimeout,
)

# Use the running interpreter so tests work regardless of PATH.
_PY = sys.executable
_ENV = {"PATH": os.path.dirname(_PY) + ":/usr/bin:/bin"}


@pytest.mark.asyncio
async def test_run_captures_stdout_no_pty() -> None:
    sess = ExecSession(
        argv=[_PY, "-c", "print('hello')"],
        cwd="/tmp",
        env=_ENV,
        use_pty=False,
    )
    result = await sess.run(timeout=10.0)
    assert isinstance(result, ExecResult)
    assert result.exit_code == 0
    assert "hello" in result.text


@pytest.mark.asyncio
async def test_run_raises_on_nonzero_exit_no_pty() -> None:
    sess = ExecSession(
        argv=[_PY, "-c", "import sys; sys.stderr.write('boom'); sys.exit(2)"],
        cwd="/tmp",
        env=_ENV,
        use_pty=False,
    )
    with pytest.raises(ExecFailed) as exc:
        await sess.run(timeout=10.0)
    assert exc.value.exit_code == 2
    assert "boom" in exc.value.stderr


@pytest.mark.asyncio
async def test_run_timeout_no_pty() -> None:
    sess = ExecSession(
        argv=[_PY, "-c", "import time; time.sleep(10)"],
        cwd="/tmp",
        env=_ENV,
        use_pty=False,
    )
    with pytest.raises(ExecTimeout):
        await sess.run(timeout=0.5)


@pytest.mark.asyncio
async def test_run_pty_captures_stdout() -> None:
    sess = ExecSession(
        argv=[_PY, "-c", "print('pty-hello')"],
        cwd="/tmp",
        env=_ENV,
        use_pty=True,
    )
    result = await sess.run(timeout=10.0)
    assert "pty-hello" in result.text
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_run_truncates_oversized_output_no_pty() -> None:
    sess = ExecSession(
        argv=[_PY, "-c", "import sys; sys.stdout.write('x' * 2_000_000)"],
        cwd="/tmp",
        env=_ENV,
        use_pty=False,
        max_output_bytes=1024,
    )
    result = await sess.run(timeout=10.0)
    # Truncated text + truncation notice; allow some slack for the notice itself.
    assert len(result.text.encode("utf-8")) <= 1024 + 200
    assert "[truncated" in result.text


# ---------------------------------------------------------------------------
# BUG 1: pipe mode must not buffer unbounded output into memory.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipe_mode_bounds_output_and_terminates_fast_no_pty() -> None:
    """A command emitting far more than the cap must be truncated near the cap
    AND must finish promptly because reading stops once the cap is exceeded
    (memory stays bounded instead of buffering the whole stream)."""
    # Emit ~256 MiB in 1 MiB chunks, sleeping between writes. If the
    # implementation read the whole stream it would take many seconds and
    # buffer 256 MiB; a bounded reader stops almost immediately.
    program = (
        "import sys, time\n"
        "chunk = 'x' * (1024 * 1024)\n"
        "for _ in range(256):\n"
        "    sys.stdout.write(chunk)\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.05)\n"
    )
    sess = ExecSession(
        argv=[_PY, "-c", program],
        cwd="/tmp",
        env=_ENV,
        use_pty=False,
        max_output_bytes=4096,
    )
    start = time.monotonic()
    # The process is killed once the cap is exceeded, so it exits non-zero
    # (SIGKILL) rather than completing cleanly. Either outcome is acceptable
    # as long as output is bounded; capture both.
    try:
        result = await sess.run(timeout=30.0)
        text = result.text
    except ExecFailed as exc:
        text = exc.stdout
    elapsed = time.monotonic() - start

    assert len(text.encode("utf-8")) <= 4096 + 200, "output was not bounded near the cap"
    # 256 chunks * 0.05s sleep = ~12.8s if fully drained. Bounded reading
    # should stop within a couple seconds.
    assert elapsed < 5.0, f"reader did not stop early (took {elapsed:.1f}s)"


# ---------------------------------------------------------------------------
# BUG 2: timeout must kill the whole process group, not just the direct child.
# ---------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.asyncio
@pytest.mark.parametrize("use_pty", [False, True])
async def test_timeout_kills_grandchild_process(tmp_path, use_pty: bool) -> None:
    """On timeout the runner must reap descendant processes, not orphan them."""
    pidfile = tmp_path / f"grandchild_{use_pty}.pid"
    # The grandchild closes inherited stdio so it does NOT hold the parent's
    # output pipe open — this isolates the bug to "did we kill the group?"
    # rather than "did the pipe drain?".
    gc_code = (
        "import os, sys, time\n"
        "os.close(0)\n"
        "[os.close(fd) for fd in (1, 2) if True]\n"
        "time.sleep(60)\n"
    )
    program = (
        "import subprocess, sys, time, os\n"
        f"gc = subprocess.Popen([sys.executable, '-c', {gc_code!r}],\n"
        "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
        "    stderr=subprocess.DEVNULL)\n"
        f"open({str(pidfile)!r}, 'w').write(str(gc.pid))\n"
        "time.sleep(60)\n"
    )
    sess = ExecSession(
        argv=[_PY, "-c", program],
        cwd="/tmp",
        env=_ENV,
        use_pty=use_pty,
    )
    with pytest.raises(ExecTimeout):
        await sess.run(timeout=1.5)

    # The grandchild PID should have been recorded before the timeout fired.
    deadline = time.monotonic() + 2.0
    while not pidfile.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pidfile.exists(), "grandchild never started / pidfile missing"
    gc_pid = int(pidfile.read_text().strip())

    # Within a generous deadline the grandchild must be dead.
    deadline = time.monotonic() + 5.0
    while _pid_alive(gc_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    alive = _pid_alive(gc_pid)
    if alive:
        # Clean up so we don't leak a 60s sleeper across the test run.
        try:
            os.kill(gc_pid, 9)
        except ProcessLookupError:
            pass
    assert not alive, f"grandchild pid {gc_pid} survived timeout kill (leak)"
