from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .base import ToolAdapter
from .errors import BadToolDefinition

_VALID_RESUME_SOURCES = {
    "none",
    "stdout_regex",
    "stderr_regex",
    "json_field",
    "sentinel",
    # caller_uuid: discode mints the session id at session creation and passes
    # it on every turn, including the first. Nothing is scraped from output, so
    # parse_resume_token must stay None-returning or it would clobber the id.
    "caller_uuid",
}

# Sources that carry no pattern: "none" and "sentinel" by construction,
# "caller_uuid" because the token is generated rather than matched.
_PATTERNLESS_SOURCES = {"none", "sentinel", "caller_uuid"}
_VALID_REPLY_EXTRACTORS = {"stdout_raw", "stdout_strip_ansi", "json_field", "stderr_fallback"}

# CSI / OSC / single-char escape sequences emitted by TUI tools — must be
# stripped before posting to Discord. (Mirrors saga_input._ANSI_RE.)
_ANSI_RE = re.compile(
    r"\x1B(?:"
    r"[@-Z\\-_]"
    r"|\["
    r"[0-?]*[ -/]*[@-~]"
    r"|\][^\x07]*\x07"
    r")"
)


def _substitute(tokens: list[str], **vars: str) -> list[str]:
    out = []
    for tok in tokens:
        for k, v in vars.items():
            tok = tok.replace("{" + k + "}", v)
        out.append(tok)
    return out


class GenericAdapter(ToolAdapter):
    def __init__(self, definition: Any) -> None:
        self._d = definition
        self._validate()

    def _validate(self) -> None:
        d = self._d
        if d.resume_token_source not in _VALID_RESUME_SOURCES:
            raise BadToolDefinition(
                d.name,
                f"resume_token_source={d.resume_token_source!r} not in {_VALID_RESUME_SOURCES}",
            )
        if d.reply_extractor not in _VALID_REPLY_EXTRACTORS:
            raise BadToolDefinition(
                d.name,
                f"reply_extractor={d.reply_extractor!r} not in {_VALID_REPLY_EXTRACTORS}",
            )
        if not isinstance(d.argv_prefix, list) or not d.argv_prefix:
            raise BadToolDefinition(d.name, "argv_prefix must be non-empty list")
        if not isinstance(d.argv_suffix, list) or not any("{prompt}" in t for t in d.argv_suffix):
            raise BadToolDefinition(d.name, "argv_suffix must contain {prompt} placeholder")
        if d.argv_resume_tokens:
            has_token = any("{token}" in t for t in d.argv_resume_tokens)
            if not has_token and d.resume_token_source != "sentinel":
                raise BadToolDefinition(
                    d.name,
                    "argv_resume_tokens missing {token} (only allowed for sentinel source)",
                )
        if d.resume_token_source not in _PATTERNLESS_SOURCES and not d.resume_token_pattern:
            raise BadToolDefinition(
                d.name,
                f"resume_token_source={d.resume_token_source} requires resume_token_pattern",
            )
        if d.reply_extractor == "json_field" and not d.reply_json_field:
            raise BadToolDefinition(d.name, "reply_extractor=json_field requires reply_json_field")

    @property
    def name(self) -> str:
        return self._d.name

    def env_allowlist(self) -> list[str]:
        return list(self._d.env_passthrough or [])

    def extra_paths(self) -> list[str]:
        return list(self._d.extra_paths or [])

    def exec_cmd(self, prompt: str, *, resume_token: str | None) -> list[str]:
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
            return _ANSI_RE.sub("", stdout or "")
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
    def _try_json(stdout: str) -> dict | None:
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
