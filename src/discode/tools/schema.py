"""One validation contract for registry mutations, seeds and execution."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from discode.security.env_scrub import FORBIDDEN_ENV

VALID_RESUME_SOURCES = frozenset(
    {"none", "stdout_regex", "stderr_regex", "json_field", "sentinel", "caller_uuid"}
)
VALID_REPLY_EXTRACTORS = frozenset(
    {"stdout_raw", "stdout_strip_ansi", "json_field", "stderr_fallback"}
)
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ENV_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ToolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    display_name: str = Field(default="", max_length=128)
    argv_prefix: list[str] = Field(min_length=1, max_length=128)
    argv_resume_tokens: list[str] = Field(default_factory=list, max_length=128)
    argv_suffix: list[str] = Field(min_length=1, max_length=128)
    uses_pty: bool = False
    resume_token_source: Literal[
        "none", "stdout_regex", "stderr_regex", "json_field", "sentinel", "caller_uuid"
    ]
    resume_token_pattern: str | None = Field(default=None, max_length=512)
    reply_extractor: Literal["stdout_raw", "stdout_strip_ansi", "json_field", "stderr_fallback"]
    reply_json_field: str | None = Field(default=None, max_length=64)
    extra_paths: list[str] = Field(default_factory=list, max_length=128)
    env_passthrough: list[str] = Field(default_factory=list, max_length=128)
    model_flag: str | None = Field(default=None, max_length=64)
    api_key_env: str | None = Field(default=None, max_length=64)
    base_url_env: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_contract(self) -> ToolConfig:
        arrays = (self.argv_prefix, self.argv_resume_tokens, self.argv_suffix, self.extra_paths)
        if any(not item or "\0" in item for items in arrays for item in items):
            raise ValueError("argument/path elements must be nonempty strings without NUL")
        if not any("{prompt}" in token for token in self.argv_suffix):
            raise ValueError("argv_suffix must contain {prompt}")
        if (
            self.argv_resume_tokens
            and self.resume_token_source != "sentinel"
            and not any("{token}" in token for token in self.argv_resume_tokens)
        ):
            raise ValueError("argv_resume_tokens must contain {token}")
        if self.resume_token_source not in {"none", "sentinel", "caller_uuid"}:
            if not self.resume_token_pattern:
                raise ValueError("resume_token_source requires resume_token_pattern")
        if self.resume_token_source in {"stdout_regex", "stderr_regex"}:
            try:
                compiled = re.compile(self.resume_token_pattern or "")
            except re.error as exc:
                raise ValueError("resume_token_pattern regex invalid") from exc
            if compiled.groups != 1:
                raise ValueError("resume_token_pattern must have exactly one capture group")
        if self.reply_extractor == "json_field" and not self.reply_json_field:
            raise ValueError("json_field requires reply_json_field")
        for key in [*self.env_passthrough, self.api_key_env, self.base_url_env]:
            if key is not None and (not ENV_PATTERN.fullmatch(key) or key in FORBIDDEN_ENV):
                raise ValueError("environment key is invalid or reserved for infrastructure")
        return self


def validate_tool_config(config: dict[str, Any], *, name: str | None = None) -> str | None:
    if name is not None and not NAME_PATTERN.fullmatch(name):
        return f"name must match {NAME_PATTERN.pattern}"
    try:
        ToolConfig.model_validate(config)
    except ValidationError as exc:
        # Avoid echoing config values, which may contain credentials in argv.
        error = exc.errors(include_input=False, include_url=False)[0]
        field = ".".join(str(part) for part in error["loc"]) or "config"
        return f"{field}: {error['msg']}"
    return None
