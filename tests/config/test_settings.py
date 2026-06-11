from __future__ import annotations

import pytest

from discode.config import Settings


def _base_env() -> dict[str, str]:
    return {
        "DISCORD_TOKEN": "x" * 50,
        "DATABASE_URL": "postgresql+asyncpg://u:p@h/db",
        "REDIS_URL": "redis://h",
        "DEPLOYMENT_SECRET_KEY": "B41Sv_DoFteZnHS5xQzdYNRLKmkGooKcjCUXXohKEco=",
        "SESSION_ISOLATION_MODE": "platform_equivalent",
    }


def test_settings_load_with_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    s = Settings()  # type: ignore[call-arg]
    assert s.DISCORD_TOKEN.startswith("x")
    assert s.RUNNER_CONTROL_BIND.startswith("127.0.0.1")


def test_settings_rejects_placeholder_token(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DISCORD_TOKEN", "DISCORD_TOKEN")
    with pytest.raises(Exception):
        Settings()  # type: ignore[call-arg]
