from __future__ import annotations

import threading
from typing import Literal

from cryptography.fernet import Fernet, MultiFernet
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDERS = frozenset(
    {
        "",
        "REPLACE_ME_WITH_RANDOM",
        "REPLACE_ME_WITH_FERNET_KEY",
        "DISCORD_TOKEN",
        "DEPLOYMENT_SECRET_KEY",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required fields
    DISCORD_TOKEN: str
    DATABASE_URL: str
    REDIS_URL: str
    DEPLOYMENT_SECRET_KEY: str

    # Restricted values
    SESSION_ISOLATION_MODE: Literal["uid_pool", "user_namespace", "platform_equivalent"]

    # Optional fields with defaults (spec §8.3)
    SESSION_UID_POOL_START: int = 10000
    SESSION_UID_POOL_SIZE: int = 64
    SESSION_CREATE_TIMEOUT_SECONDS: int = 60
    RUNNER_JOB_PEL_IDLE_MS: int = 60000
    INPUT_TENTATIVE_LEASE_SECONDS: int = 30
    RUNNER_CONTROL_BIND: str = "127.0.0.1:8788"
    # Shared secret for bot→runner control API calls. Required when RUNNER_CONTROL_BIND
    # is not loopback (e.g. private-network multi-host deployments).
    RUNNER_CONTROL_AUTH: str = ""
    ENABLE_MESSAGE_CONTENT_INPUT: bool = False
    EMERGENCY_STOP: int = 0

    @field_validator("DISCORD_TOKEN", "DATABASE_URL", "REDIS_URL", mode="before")
    @classmethod
    def reject_placeholders(cls, v: object) -> object:
        if isinstance(v, str) and v in _PLACEHOLDERS:
            raise ValueError(f"field value is a placeholder or empty: {v!r}")
        return v

    @field_validator("DEPLOYMENT_SECRET_KEY", mode="before")
    @classmethod
    def validate_fernet_key(cls, v: object) -> object:
        if not isinstance(v, str) or v in _PLACEHOLDERS:
            raise ValueError(f"DEPLOYMENT_SECRET_KEY is a placeholder or empty: {v!r}")
        try:
            keys = [Fernet(k.strip().encode()) for k in v.split(",")]
            MultiFernet(keys)
        except Exception as exc:
            raise ValueError(f"DEPLOYMENT_SECRET_KEY is not a valid Fernet key CSV: {exc}") from exc
        return v

    @property
    def database_url(self) -> str:
        return self.DATABASE_URL

    @property
    def deployment_secret_key(self) -> str:
        return self.DEPLOYMENT_SECRET_KEY

    @model_validator(mode="after")
    def check_lease_invariant(self) -> Settings:
        if self.RUNNER_JOB_PEL_IDLE_MS <= self.INPUT_TENTATIVE_LEASE_SECONDS * 1000:
            threshold = self.INPUT_TENTATIVE_LEASE_SECONDS * 1000
            raise ValueError(
                "RUNNER_JOB_PEL_IDLE_MS must be greater than INPUT_TENTATIVE_LEASE_SECONDS * 1000 "
                f"(got {self.RUNNER_JOB_PEL_IDLE_MS} <= {threshold})"
            )
        return self


class _LazySettings:
    """Defer ``Settings()`` construction until first attribute access.

    This keeps the module importable in test environments that don't provide
    the required env vars, while still ensuring the singleton is built (and
    validated) exactly once when it is first used.
    """

    _instance: Settings | None = None
    _lock: threading.Lock = threading.Lock()

    def _load(self) -> Settings:
        if self._instance is None:
            with self._lock:
                if self._instance is None:
                    self._instance = Settings()  # type: ignore[call-arg]
        return self._instance

    def __getattr__(self, name: str) -> object:
        return getattr(self._load(), name)


settings: Settings = _LazySettings()  # type: ignore[assignment]
