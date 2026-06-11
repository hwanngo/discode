from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from discode.config import Settings
from discode.runtime import AppRuntime


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        DISCORD_TOKEN="token",
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db",
        REDIS_URL="redis://localhost:6379/0",
        DEPLOYMENT_SECRET_KEY="eW91ci1zZWNyZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=",
        SESSION_ISOLATION_MODE="platform_equivalent",
    )


@pytest.mark.asyncio
async def test_runtime_close_disposes_engine_and_redis() -> None:
    engine = Mock()
    engine.dispose = AsyncMock()
    redis = AsyncMock()
    runtime = AppRuntime(settings=_settings(), engine=engine, db_factory=Mock(), redis=redis)

    await runtime.close()

    engine.dispose.assert_awaited_once()
    redis.aclose.assert_awaited_once()


def test_runtime_from_settings_builds_dependencies() -> None:
    settings = _settings()
    with (
        patch("discode.runtime.make_engine") as make_engine,
        patch("discode.runtime.make_session_factory") as make_session_factory,
        patch("discode.runtime.Redis.from_url") as from_url,
    ):
        make_engine.return_value = "engine"
        make_session_factory.return_value = "factory"
        from_url.return_value = "redis"

        runtime = AppRuntime.from_settings(settings)

    assert runtime.engine == "engine"
    assert runtime.db_factory == "factory"
    assert runtime.redis == "redis"
    make_engine.assert_called_once_with(settings.DATABASE_URL)
    make_session_factory.assert_called_once_with("engine")
    from_url.assert_called_once_with(settings.REDIS_URL, decode_responses=False)
