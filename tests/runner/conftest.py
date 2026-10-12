import pytest
from cryptography.fernet import Fernet

from discode.config import settings


@pytest.fixture(autouse=True)
def trusted_test_runner(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "offline-test-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost/0")
    monkeypatch.setenv("DEPLOYMENT_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SESSION_ISOLATION_MODE", "trusted_host")
    monkeypatch.setenv("RUNNER_CONTROL_AUTH", "test-bearer")
    monkeypatch.setattr(settings, "_instance", None)
