from __future__ import annotations

import threading
from typing import Literal

from cryptography.fernet import Fernet, MultiFernet
from pydantic import Field, field_validator
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
        hide_input_in_errors=True,
    )

    # Required fields
    DISCORD_TOKEN: str
    DATABASE_URL: str
    REDIS_URL: str
    DEPLOYMENT_SECRET_KEY: str

    # Restricted values
    SESSION_ISOLATION_MODE: Literal[
        "trusted_host", "uid_pool", "user_namespace", "platform_equivalent"
    ]

    # Optional fields with defaults (spec §8.3)
    SESSION_CREATE_TIMEOUT_SECONDS: int = Field(60, gt=0)
    RUNNER_JOB_PEL_IDLE_MS: int = Field(60000, gt=0)
    RUNNER_CONTROL_BIND: str = "127.0.0.1:8788"
    # Shared secret for bot→runner control API calls. Required on every bind,
    # including loopback (agents share the host); the runner refuses to start without it.
    RUNNER_CONTROL_AUTH: str = ""
    ENABLE_MESSAGE_CONTENT_INPUT: bool = False
    EMERGENCY_STOP: int = Field(0, ge=0, le=1)

    PLATFORM_ADMIN_USER_IDS: str = ""
    # Comma-separated guild IDs the bot serves. Empty denies every guild (fail closed).
    ALLOWED_GUILD_IDS: str = ""
    DISCORD_GUILD_COMMANDS_ONLY: bool = True
    RUNNER_HOST_ID: str = "local-dev"
    RUNNER_HEARTBEAT_INTERVAL_SECONDS: int = Field(5, gt=0)
    RUNNER_HEARTBEAT_STALE_SECONDS: int = Field(15, gt=0)
    RUNNER_DRAIN_TIMEOUT_SECONDS: int = Field(25, gt=0)
    RUNNER_JOB_MAX_DELIVERIES: int = Field(5, gt=0)
    DISPATCHER_PEL_IDLE_MS: int = Field(60000, gt=0)
    DISPATCHER_MAX_DELIVERIES: int = Field(5, gt=0)
    OUTBOX_BATCH_SIZE: int = Field(64, gt=0, le=10000)
    OUTBOX_IDLE_SLEEP_MS: int = Field(200, gt=0)
    OUTBOX_MAX_ATTEMPTS: int = Field(50, gt=0)
    OUTBOX_BASE_BACKOFF_MS: int = Field(100, gt=0)
    OUTBOX_MAX_BACKOFF_MS: int = Field(60000, gt=0)
    RESUME_STUCK_THRESHOLD_SECONDS: int = Field(300, gt=0)
    STOP_STUCK_THRESHOLD_SECONDS: int = Field(600, gt=0)
    INPUT_IDEMPOTENCY_RETENTION: str = "30d"
    DEFAULT_IDLE_TIMEOUT_MINUTES: int = Field(60, gt=0)
    DEFAULT_ARCHIVE_TIMEOUT_MINUTES: int = Field(120, gt=0)
    DEFAULT_STOP_TIMEOUT_HOURS: int = Field(24, gt=0)
    EVENT_RETENTION_DAYS: int = Field(90, gt=0)
    OUTBOX_RETENTION_DAYS: int = Field(7, gt=0)
    ORPHAN_RECOVERY_TTL_HOURS: int = Field(24, gt=0)
    MAX_ACTIVE_SESSIONS_PER_USER: int = Field(10, gt=0)
    MAX_OUTPUT_BYTES_PER_SESSION_PER_MIN: int = Field(262144, gt=0)
    MAX_INPUT_MESSAGES_PER_SESSION_PER_MIN: int = Field(20, gt=0)
    MAX_INPUT_BYTES_PER_MESSAGE: int = Field(8192, gt=0)
    MAX_INPUT_BYTES_PER_SESSION_PER_MIN: int = Field(163840, gt=0)
    BOT_METRICS_BIND: str = "127.0.0.1:9101"
    RUNNER_METRICS_BIND: str = "127.0.0.1:9102"
    DISPATCHER_METRICS_BIND: str = "127.0.0.1:9103"
    JANITOR_METRICS_BIND: str = "127.0.0.1:9104"
    JANITOR_SWEEP_INTERVAL_SECONDS: float = Field(60, gt=0)
    REDIS_STREAM_RETENTION_HOURS: int = Field(24, gt=0)
    RETENTION_BATCH_SIZE: int = Field(1000, gt=0, le=10000)
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    DB_POOL_SIZE: int = Field(20, gt=0)
    DB_MAX_OVERFLOW: int = Field(10, ge=0)

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("INPUT_IDEMPOTENCY_RETENTION")
    @classmethod
    def validate_retention(cls, value: str) -> str:
        if value != "never" and not (
            value.endswith("d") and value[:-1].isdigit() and int(value[:-1]) > 0
        ):
            raise ValueError(
                "INPUT_IDEMPOTENCY_RETENTION must be positive days, e.g. 30d, or never"
            )
        return value

    @property
    def platform_admin_user_ids(self) -> frozenset[str]:
        return frozenset(x.strip() for x in self.PLATFORM_ADMIN_USER_IDS.split(",") if x.strip())

    @property
    def allowed_guild_ids(self) -> frozenset[str]:
        return frozenset(x.strip() for x in self.ALLOWED_GUILD_IDS.split(",") if x.strip())

    @field_validator("DISCORD_TOKEN", "DATABASE_URL", "REDIS_URL", mode="before")
    @classmethod
    def reject_placeholders(cls, v: object) -> object:
        if isinstance(v, str) and v in _PLACEHOLDERS:
            raise ValueError("field value is a placeholder or empty")
        return v

    @field_validator("DEPLOYMENT_SECRET_KEY", mode="before")
    @classmethod
    def validate_fernet_key(cls, v: object) -> object:
        if not isinstance(v, str) or v in _PLACEHOLDERS:
            raise ValueError("DEPLOYMENT_SECRET_KEY is a placeholder or empty")
        try:
            keys = [Fernet(k.strip().encode()) for k in v.split(",")]
            MultiFernet(keys)
        except Exception as exc:
            raise ValueError("DEPLOYMENT_SECRET_KEY is not a valid Fernet key CSV") from exc
        return v

    @property
    def database_url(self) -> str:
        return self.DATABASE_URL

    @property
    def deployment_secret_key(self) -> str:
        return self.DEPLOYMENT_SECRET_KEY


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


def get_settings() -> Settings:
    """Validated shared settings, including dotenv, for every process component."""
    return settings
