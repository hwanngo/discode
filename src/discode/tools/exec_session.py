from __future__ import annotations

import asyncio
import os
import pty
import signal
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


class ExecFailed(Exception):
    """Subprocess exited with non-zero status; raw streams are not in the log message."""

    def __init__(self, exit_code: int, stderr: str, stdout: str = "") -> None:
        super().__init__(f"exec failed: exit={exit_code}")
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout


class ExecTimeout(Exception):
    """Subprocess exceeded the wall-clock timeout and was killed."""


@dataclass(frozen=True)
class ExecResult:
    text: str
    stderr: str
    exit_code: int


_DEFAULT_MAX_OUTPUT = 1024 * 1024
# Output beyond max_output_bytes is drained and discarded; only a runaway stream
# past this many bytes per stream gets the process group killed.
_DEFAULT_KILL_OUTPUT = 16 * 1024 * 1024
# After the main process exits, background children that inherited the output
# descriptors get this long before the group is killed so reads can finish.
_PIPE_GRACE_SECONDS = 2.0
# Upper bound on cleanup; a descendant that escaped the group (setsid) may keep
# a descriptor open forever.
_REAP_TIMEOUT_SECONDS = 5.0
_EXIT_POLL_SECONDS = 0.05


class ExecSession:
    """Run one process group with bounded output and cancellation-safe cleanup.

    This primitive is not a sandbox. The runner must enforce its execution policy
    before constructing it. A deadline covers both output collection and process exit.
    """

    def __init__(
        self,
        *,
        argv: Sequence[str],
        cwd: str,
        env: dict[str, str],
        use_pty: bool,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT,
        kill_output_bytes: int | None = None,
    ) -> None:
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        self._kill_bytes = max(kill_output_bytes or _DEFAULT_KILL_OUTPUT, max_output_bytes)
        self._killed_for_output = False
        self._argv = list(argv)
        self._cwd = cwd
        self._env = env
        self._use_pty = use_pty
        self._max_bytes = max_output_bytes

    async def run(self, timeout: float = 300.0) -> ExecResult:
        if self._use_pty:
            return await self._run_pty(timeout)
        return await self._run_pipe(timeout)

    @staticmethod
    def _killpg(proc: asyncio.subprocess.Process) -> None:
        # start_new_session makes pid the group id. Use it even if the leader
        # has exited, so descendants retaining output descriptors are killed.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    async def _read_bounded(
        self,
        stream: asyncio.StreamReader | None,
        on_runaway: Callable[[], None],
    ) -> tuple[bytes, bool]:
        """Keep up to max_output_bytes, drain the rest; call on_runaway past the kill cap."""
        if stream is None:
            return b"", False
        chunks: list[bytes] = []
        kept_total = 0
        seen = 0
        try:
            while data := await stream.read(65536):
                seen += len(data)
                kept = data[: self._max_bytes - kept_total]
                if kept:
                    chunks.append(kept)
                    kept_total += len(kept)
                if seen > self._kill_bytes and not self._killed_for_output:
                    self._killed_for_output = True
                    on_runaway()
                # Keep draining after truncation or a kill: a paused pipe transport
                # would otherwise block the writer and Process.wait().
        except asyncio.CancelledError:
            # Only ExecSession cancels its own readers (a descriptor held by a
            # process outside the group never reaches EOF); keep what was read.
            pass
        return b"".join(chunks), seen > kept_total

    @staticmethod
    async def _discard(stream: asyncio.StreamReader | None) -> None:
        if stream is not None:
            while await stream.read(65536):
                pass

    async def _reap(self, proc: asyncio.subprocess.Process) -> None:
        self._killpg(proc)
        try:
            async with asyncio.timeout(_REAP_TIMEOUT_SECONDS):
                await asyncio.gather(
                    self._discard(proc.stdout), self._discard(proc.stderr), proc.wait()
                )
        except TimeoutError:
            # A descendant outside the process group still holds a descriptor.
            pass

    async def _await_streams(
        self, proc: asyncio.subprocess.Process, readers: Sequence[asyncio.Future[Any]]
    ) -> None:
        """Wait for the main process, then give inherited descriptors a short grace."""
        # Not proc.wait(): asyncio's wait() also blocks until every pipe closes,
        # which a backgrounded child holding stdout would delay until the timeout.
        while proc.returncode is None:
            await asyncio.sleep(_EXIT_POLL_SECONDS)
        _, pending = await asyncio.wait(readers, timeout=_PIPE_GRACE_SECONDS)
        # Background children (e.g. `server &`) must not outlive the turn unsupervised.
        self._killpg(proc)
        if pending:
            # A descendant that left the group (setsid) can hold a descriptor forever.
            _, pending = await asyncio.wait(pending, timeout=_REAP_TIMEOUT_SECONDS)
            for reader in pending:
                reader.cancel()
            if pending:
                await asyncio.wait(pending)

    def _result(
        self, stdout: bytes, stderr: bytes, out_truncated: bool, err_truncated: bool, code: int
    ) -> ExecResult:
        notice = f"\n[truncated at {self._max_bytes} bytes]"
        out = stdout.decode("utf-8", errors="replace") + (notice if out_truncated else "")
        err = stderr.decode("utf-8", errors="replace") + (notice if err_truncated else "")
        if self._killed_for_output:
            raise ExecFailed(
                code or -signal.SIGKILL,
                err + f"\n[killed: output exceeded {self._kill_bytes} bytes]",
                out,
            )
        if code:
            raise ExecFailed(code, err, out)
        return ExecResult(out, err, code)

    async def _run_pipe(self, timeout: float) -> ExecResult:
        proc = await asyncio.create_subprocess_exec(
            *self._argv,
            cwd=self._cwd,
            env=self._env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        out_task = asyncio.ensure_future(
            self._read_bounded(proc.stdout, lambda: self._killpg(proc))
        )
        err_task = asyncio.ensure_future(
            self._read_bounded(proc.stderr, lambda: self._killpg(proc))
        )
        try:
            async with asyncio.timeout(timeout):
                await self._await_streams(proc, (out_task, err_task))
        except BaseException as exc:
            out_task.cancel()
            err_task.cancel()
            await asyncio.gather(out_task, err_task, return_exceptions=True)
            await asyncio.shield(self._reap(proc))
            if isinstance(exc, TimeoutError):
                raise ExecTimeout(f"exec exceeded {timeout}s") from exc
            raise
        out, out_truncated = out_task.result()
        err, err_truncated = err_task.result()
        return self._result(out, err, out_truncated, err_truncated, proc.returncode or 0)

    async def _run_pty(self, timeout: float) -> ExecResult:
        master_fd, slave_fd = pty.openpty()
        loop = asyncio.get_running_loop()
        try:
            os.set_blocking(master_fd, False)
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self._argv,
                    cwd=self._cwd,
                    env=self._env,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
            finally:
                os.close(slave_fd)

            chunks: list[bytes] = []
            total = 0
            seen = 0
            eof = asyncio.Event()

            def readable() -> None:
                nonlocal total, seen
                try:
                    data = os.read(master_fd, 65536)
                except BlockingIOError:
                    return
                except OSError:
                    data = b""
                if not data:
                    loop.remove_reader(master_fd)
                    eof.set()
                    return
                seen += len(data)
                kept = data[: self._max_bytes - total]
                if kept:
                    chunks.append(kept)
                    total += len(kept)
                if seen > self._kill_bytes and not self._killed_for_output:
                    self._killed_for_output = True
                    self._killpg(proc)

            loop.add_reader(master_fd, readable)
            err_task = asyncio.ensure_future(
                self._read_bounded(proc.stderr, lambda: self._killpg(proc))
            )
            eof_task = asyncio.ensure_future(eof.wait())
            try:
                async with asyncio.timeout(timeout):
                    await self._await_streams(proc, (err_task, eof_task))
            except BaseException as exc:
                err_task.cancel()
                eof_task.cancel()
                await asyncio.gather(err_task, eof_task, return_exceptions=True)
                await asyncio.shield(self._reap(proc))
                if isinstance(exc, TimeoutError):
                    raise ExecTimeout(f"exec exceeded {timeout}s") from exc
                raise
            err, err_truncated = err_task.result()
            return self._result(
                b"".join(chunks), err, seen > total, err_truncated, proc.returncode or 0
            )
        finally:
            loop.remove_reader(master_fd)
            os.close(master_fd)
