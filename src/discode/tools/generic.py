from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .base import ToolAdapter
from .errors import BadToolDefinition
from .schema import (
    ToolConfig,
    validate_tool_config,
)
from .text import strip_ansi


def _substitute(tokens: list[str], **vars: str) -> list[str]:
    out = []
    for tok in tokens:
        for k, v in vars.items():
            tok = tok.replace("{" + k + "}", v)
        out.append(tok)
    return out


class AmbiguousPrompt(ValueError):
    """The prompt starts with '-' and the tool's argv has no ``--`` guard."""


class GenericAdapter(ToolAdapter):
    def __init__(self, definition: Any) -> None:
        self._d = definition
        self._validate()

    def _validate(self) -> None:
        config = {
            name: getattr(self._d, name)
            for name in ToolConfig.model_fields
            if hasattr(self._d, name)
        }
        error = validate_tool_config(config, name=str(self._d.name))
        if error:
            raise BadToolDefinition(str(self._d.name), error)

    @property
    def name(self) -> str:
        return str(self._d.name)

    def env_allowlist(self) -> list[str]:
        return list(self._d.env_passthrough or [])

    def extra_paths(self) -> list[str]:
        return list(self._d.extra_paths or [])

    def exec_cmd(self, prompt: str, *, resume_token: str | None) -> list[str]:
        if self._d.resume_token_source == "sentinel":
            raise ValueError(
                "sentinel resume is disabled: the CLI must support an explicit session identity"
            )
        # Reject ambiguous positional prompts rather than guessing which CLIs
        # support an end-of-options delimiter. Definitions can use --message=
        # or an explicit -- before a standalone prompt to accept leading dashes.
        if prompt.lstrip().startswith("-"):
            suffix = list(self._d.argv_suffix)
            for index, token in enumerate(suffix):
                if token == "{prompt}" and "--" not in suffix[:index]:
                    raise AmbiguousPrompt(
                        "leading-dash prompt requires a tool definition with an explicit -- guard"
                    )
        argv = list(self._d.argv_prefix)
        if resume_token and self._d.argv_resume_tokens:
            argv.extend(_substitute(list(self._d.argv_resume_tokens), token=resume_token))
        argv.extend(_substitute(list(self._d.argv_suffix), prompt=prompt))
        return argv

    def exec_uses_pty(self) -> bool:
        return bool(self._d.uses_pty)

    def mint_initial_token(self) -> str | None:
        if self._d.resume_token_source == "caller_uuid":
            return str(uuid.uuid4())
        return None

    def parse_resume_token(self, stdout: str, stderr: str) -> str | None:
        token = self._parse_resume_token(stdout, stderr)
        # Tokens come from tool output and are spliced into argv: one that starts
        # with '-' or contains whitespace would be parsed as flags.
        if token is not None and (token.startswith("-") or any(c.isspace() for c in token)):
            return None
        return token

    def _parse_resume_token(self, stdout: str, stderr: str) -> str | None:
        d = self._d
        src = d.resume_token_source
        if src == "none":
            return None
        if src == "sentinel":
            return d.resume_token_pattern or None
        if src == "stdout_regex":
            m = re.search(d.resume_token_pattern, stdout or "")
            return m.group(1) if m else None
        if src == "stderr_regex":
            m = re.search(d.resume_token_pattern, stderr or "")
            return m.group(1) if m else None
        if src == "json_field":
            payload = self._try_json(stdout)
            if payload is None:
                return None
            v = payload.get(d.resume_token_pattern)
            return str(v) if v else None
        return None

    def extract_reply(self, stdout: str, stderr: str) -> str:
        d = self._d
        ex = d.reply_extractor
        if ex == "stdout_raw":
            return stdout
        if ex == "stdout_strip_ansi":
            return strip_ansi(stdout or "")
        if ex == "json_field":
            payload = self._try_json(stdout)
            if payload is None:
                return stdout
            v = payload.get(d.reply_json_field)
            return v if isinstance(v, str) and v else stdout
        if ex == "stderr_fallback":
            return stdout if stdout else stderr
        return stdout

    @staticmethod
    def _try_json(stdout: str) -> dict[str, Any] | None:
        if not stdout:
            return None
        s = stdout.strip()
        if not s.startswith("{"):
            return None
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None
