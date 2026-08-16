from __future__ import annotations

from abc import ABC, abstractmethod


class ToolAdapter(ABC):
    """Per-tool metadata and the per-turn exec contract."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name: 'claude', 'opencode', or 'codex'."""

    def env_allowlist(self) -> list[str]:
        """Environment variable names that should always pass through to the
        worker subprocess. Returned by adapters for documentation; the runner
        currently inherits the full process env.
        """
        return []

    def extra_paths(self) -> list[str]:
        """Additional PATH entries to prepend when locating the tool binary.

        Returned values may be absolute paths OR ``~``-prefixed paths
        (e.g. ``"~/.local/bin"``). The runner expands ``~`` against the
        process HOME, drops entries that don't exist on disk, and dedups
        before joining into PATH.
        """
        return []

    @abstractmethod
    def exec_cmd(self, prompt: str, *, resume_token: str | None) -> list[str]:
        """argv for one-shot execution of this tool against ``prompt``.

        If ``resume_token`` is set, the argv must include the tool's resume
        flag so multi-turn continuity works.
        """

    def exec_uses_pty(self) -> bool:
        """Whether ``exec_cmd`` requires a PTY (codex/opencode do; claude does not)."""
        return False

    def mint_initial_token(self) -> str | None:
        """Resume token to assign at session creation, before the first turn.

        Most tools emit their own session id, which the runner scrapes from
        output via :meth:`parse_resume_token` after turn one. Tools that accept
        a *caller-assigned* id instead (e.g. ``pi --session-id``) return one
        here, so every turn — including the first — names the same session.
        ``None`` means "no token until the tool gives us one".
        """
        return None

    def parse_resume_token(self, stdout: str, stderr: str) -> str | None:
        """Extract a fresh resume token from worker output; ``None`` if absent."""
        return None

    def extract_reply(self, stdout: str, stderr: str) -> str:
        """Return the user-facing reply text from raw worker output.

        Default: stdout. Tools that emit structured output (e.g. claude
        --output-format json) override this to parse the response field.
        """
        return stdout
