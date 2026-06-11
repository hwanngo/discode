from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from discode.config import Settings

# A valid Fernet key for use in tests
VALID_KEY = Fernet.generate_key().decode()

VALID_BASE = {
    "DISCORD_TOKEN": "real-token-abc123",
    "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/db",
    "REDIS_URL": "redis://localhost:6379/0",
    "DEPLOYMENT_SECRET_KEY": VALID_KEY,
    "SESSION_ISOLATION_MODE": "platform_equivalent",
}


def make_settings(**overrides: object) -> Settings:
    kwargs = {**VALID_BASE, **overrides}
    return Settings(_env_file=None, **kwargs)


class TestDiscordToken:
    def test_empty_token_raises(self) -> None:
        with pytest.raises(ValidationError):
            make_settings(DISCORD_TOKEN="")

    def test_placeholder_replace_me_raises(self) -> None:
        with pytest.raises(ValidationError):
            make_settings(DISCORD_TOKEN="REPLACE_ME_WITH_RANDOM")

    def test_placeholder_fernet_raises(self) -> None:
        with pytest.raises(ValidationError):
            make_settings(DISCORD_TOKEN="REPLACE_ME_WITH_FERNET_KEY")

    def test_valid_token_works(self) -> None:
        s = make_settings()
        assert s.DISCORD_TOKEN == "real-token-abc123"


class TestValidSettings:
    def test_all_valid_fields_accepted(self) -> None:
        s = make_settings()
        assert s.SESSION_ISOLATION_MODE == "platform_equivalent"
        assert s.SESSION_UID_POOL_START == 10000
        assert s.SESSION_UID_POOL_SIZE == 64

    def test_uid_pool_mode(self) -> None:
        s = make_settings(SESSION_ISOLATION_MODE="uid_pool")
        assert s.SESSION_ISOLATION_MODE == "uid_pool"

    def test_user_namespace_mode(self) -> None:
        s = make_settings(SESSION_ISOLATION_MODE="user_namespace")
        assert s.SESSION_ISOLATION_MODE == "user_namespace"


class TestSessionIsolationMode:
    def test_none_mode_raises(self) -> None:
        with pytest.raises(ValidationError):
            make_settings(SESSION_ISOLATION_MODE="none")

    def test_unset_raises(self) -> None:
        # Remove SESSION_ISOLATION_MODE from kwargs entirely
        kwargs = {k: v for k, v in VALID_BASE.items() if k != "SESSION_ISOLATION_MODE"}
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **kwargs)

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValidationError):
            make_settings(SESSION_ISOLATION_MODE="docker")


class TestDeploymentSecretKey:
    def test_valid_fernet_key_works(self) -> None:
        key = Fernet.generate_key().decode()
        s = make_settings(DEPLOYMENT_SECRET_KEY=key)
        assert s.DEPLOYMENT_SECRET_KEY == key

    def test_multiple_valid_keys_work(self) -> None:
        key1 = Fernet.generate_key().decode()
        key2 = Fernet.generate_key().decode()
        s = make_settings(DEPLOYMENT_SECRET_KEY=f"{key1},{key2}")
        assert "," in s.DEPLOYMENT_SECRET_KEY

    def test_bad_fernet_key_raises(self) -> None:
        with pytest.raises(ValidationError):
            make_settings(DEPLOYMENT_SECRET_KEY="not-a-valid-fernet-key")

    def test_placeholder_raises(self) -> None:
        with pytest.raises(ValidationError):
            make_settings(DEPLOYMENT_SECRET_KEY="")

    def test_replace_me_placeholder_raises(self) -> None:
        with pytest.raises(ValidationError):
            make_settings(DEPLOYMENT_SECRET_KEY="REPLACE_ME_WITH_FERNET_KEY")


class TestLeaseInvariant:
    def test_violation_raises(self) -> None:
        # RUNNER_JOB_PEL_IDLE_MS <= INPUT_TENTATIVE_LEASE_SECONDS * 1000
        with pytest.raises(ValidationError):
            make_settings(RUNNER_JOB_PEL_IDLE_MS=29000, INPUT_TENTATIVE_LEASE_SECONDS=30)

    def test_equal_raises(self) -> None:
        with pytest.raises(ValidationError):
            make_settings(RUNNER_JOB_PEL_IDLE_MS=30000, INPUT_TENTATIVE_LEASE_SECONDS=30)

    def test_valid_invariant_passes(self) -> None:
        s = make_settings(RUNNER_JOB_PEL_IDLE_MS=60000, INPUT_TENTATIVE_LEASE_SECONDS=30)
        assert s.RUNNER_JOB_PEL_IDLE_MS > s.INPUT_TENTATIVE_LEASE_SECONDS * 1000
