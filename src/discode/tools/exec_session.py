from __future__ import annotations

import asyncio
import os
import pty
import signal
from collections.abc import Callable, Sequence
from dataclasses import dataclass


class ExecFailed(Exception):
    """Subprocess exited with non-zero status."""

    def __init__(self, exit_code: int, stderr: str, stdout: str = "") -> None:
        # Include both streams in the message: some tools (claude --print)
        # write errors to stdout while still exiting non-zero.
        super().__init__(
            f"exec failed: exit={exit_code} stderr={stderr[:200]!r} stdout={stdout[:200]!r}"
        )
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


_DEFAULT_MAX_OUTPUT = 1 * 1024 * 1024  # 1 MiB


class ExecSession:
    """Run a single subprocess turn and capture its stdout.

    PTY mode allocates a pseudo-terminal so tools that refuse non-tty stdout
    (codex, opencode) work. Plain-pipe mode is faster and simpler for tools
    that don't need it (claude --print).
    """

    def __init__(
        self,
        *,
        argv: Sequence[str],
        cwd: str,
        env: dict[str, str],
        use_pty: bool,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT,
    ) -> None:
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
        """SIGKILL the child's whole process group so descendants don't orphan.

        The child is spawned with start_new_session=True, making it a session/
        process-group leader. Killing the group reaps build/test grandchildren
        too. Guarded against the process already being gone.
        """
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass

    async def _read_bounded(
        self,
        stream: asyncio.StreamReader | None,
        on_truncate: Callable[[], None],
    ) -> tuple[bytes, bool]:
        """Stream-read up to ``max_bytes`` then stop, so memory stays bounded
        no matter how much the process emits. Returns (data, truncated).

        The moment the cap is exceeded ``on_truncate`` is invoked so the caller
        can kill the process group immediately — otherwise a process still
        writing to the *other* pipe (which we've stopped reading) would block on
        a full kernel buffer and never reach EOF, deadlocking the drain.
        """
        if stream is None:
            return b"", False
        chunks: list[bytes] = []
        total = 0
        truncated = False
        while total < self._max_bytes:
            data = await stream.read(min(65536, self._max_bytes - total))
            if not data:
                break
            chunks.append(data)
            total += len(data)
        else:
            # Cap reached; peek one more byte to see if output is still pending.
            extra = await stream.read(1)
            if extra:
                truncated = True
                on_truncate()
        return b"".join(chunks), truncated

    async def _run_pipe(self, timeout: float) -> ExecResult:
        proc = await asyncio.create_subprocess_exec(
            *self._argv,
            cwd=self._cwd,
            env=self._env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

        killed_for_truncation = False

        def _on_truncate() -> None:
            nonlocal killed_for_truncation
            killed_for_truncation = True
            # Kill the group now so any blocked writer is released and both
            # bounded reads can observe EOF promptly.
            self._killpg(proc)

        async def _drain() -> tuple[bytes, bool, bytes]:
            # Read both streams concurrently, each bounded.
            stdout_res, stderr_res = await asyncio.gather(
                self._read_bounded(proc.stdout, _on_truncate),
                self._read_bounded(proc.stderr, _on_truncate),
            )
            stdout_b, out_trunc = stdout_res
            stderr_b, _ = stderr_res
            return stdout_b, out_trunc, stderr_b

        try:
            stdout_b, truncated, stderr_b = await asyncio.wait_for(_drain(), timeout=timeout)
        except TimeoutError:
            self._killpg(proc)
            await proc.wait()
            raise ExecTimeout(f"exec exceeded {timeout}s")

        await proc.wait()

        stdout = stdout_b.decode("utf-8", errors="replace")
        if truncated:
            stdout += f"\n[truncated at {self._max_bytes} bytes]"
        stderr = stderr_b.decode("utf-8", errors="replace")
        exit_code = proc.returncode or 0
        # When we force-killed after truncation, a non-zero/None exit is expected
        # and must not be reported as a failure.
        if exit_code != 0 and not killed_for_truncation:
            raise ExecFailed(exit_code, stderr, stdout)
        return ExecResult(text=stdout, stderr=stderr, exit_code=exit_code)

    async def _run_pty(self, timeout: float) -> ExecResult:
        master_fd, slave_fd = pty.openpty()
        # Set master non-blocking so the reader callback never blocks the loop.
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
            # Parent doesn't need the slave end; child inherited it.
            os.close(slave_fd)

        loop = asyncio.get_running_loop()

        chunks: list[bytes] = []
        total = 0
        truncated = False
        eof_event = asyncio.Event()

        def _on_readable() -> None:
            nonlocal total, truncated
            try:
                data = os.read(master_fd, 4096)
            except OSError:
                # EIO / child exited — PTY hangup
                loop.remove_reader(master_fd)
                eof_event.set()
                return
            if not data:
                loop.remove_reader(master_fd)
                eof_event.set()
                return
            if not truncated:
                if total + len(data) > self._max_bytes:
                    keep = self._max_bytes - total
                    chunks.append(data[:keep])
                    total = self._max_bytes
                    truncated = True
                    # Cap reached: stop reading and kill the group so the
                    # process can't keep producing unbounded output.
                    loop.remove_reader(master_fd)
                    self._killpg(proc)
                    eof_event.set()
                else:
                    chunks.append(data)
                    total += len(data)

        loop.add_reader(master_fd, _on_readable)

        stderr_b = b""
        timed_out = False
        try:
            # Wait for both process exit and PTY EOF (may arrive slightly after exit).
            exit_task = asyncio.ensure_future(proc.wait())
            stderr_task = asyncio.ensure_future(proc.stderr.read()) if proc.stderr else None
            eof_task = asyncio.ensure_future(eof_event.wait())

            tasks_to_gather: list[asyncio.Future] = [exit_task, eof_task]
            if stderr_task is not None:
                tasks_to_gather.append(stderr_task)

            try:
                await asyncio.wait_for(asyncio.gather(*tasks_to_gather), timeout=timeout)
            except TimeoutError:
                timed_out = True
                self._killpg(proc)
                await proc.wait()
            finally:
                # Cancel any tasks that are still pending.
                for t in tasks_to_gather:
                    if not t.done():
                        t.cancel()
                        try:
                            await t
                        except asyncio.CancelledError, Exception:
                            pass

            if stderr_task is not None and not stderr_task.cancelled() and stderr_task.done():
                try:
                    stderr_b = stderr_task.result()
                except Exception:
                    stderr_b = b""
        finally:
            try:
                loop.remove_reader(master_fd)
            except Exception:
                pass
            try:
                os.close(master_fd)
            except OSError:
                pass

        if timed_out:
            raise ExecTimeout(f"exec exceeded {timeout}s")

        stdout = b"".join(chunks).decode("utf-8", errors="replace")
        if truncated:
            stdout += f"\n[truncated at {self._max_bytes} bytes]"
        stderr = stderr_b.decode("utf-8", errors="replace")
        exit_code = proc.returncode or 0
        if exit_code != 0:
            raise ExecFailed(exit_code, stderr, stdout)
        return ExecResult(text=stdout, stderr=stderr, exit_code=exit_code)

    def _decode_truncate(self, data: bytes) -> str:
        if len(data) > self._max_bytes:
            text = data[: self._max_bytes].decode("utf-8", errors="replace")
            return text + f"\n[truncated at {self._max_bytes} bytes]"
        return data.decode("utf-8", errors="replace")
