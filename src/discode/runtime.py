from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.config import Settings
from discode.db.engine import make_engine, make_session_factory


@dataclass
class AppRuntime:
    settings: Settings
    engine: Any
    db_factory: async_sessionmaker[AsyncSession]
    redis: Redis

    @classmethod
    def from_env(cls) -> AppRuntime:
        settings = Settings()  # type: ignore[call-arg]
        return cls.from_settings(settings)

    @classmethod
    def from_settings(cls, settings: Settings) -> AppRuntime:
        engine = make_engine(settings.DATABASE_URL)
        db_factory = make_session_factory(engine)
        redis = Redis.from_url(settings.REDIS_URL, decode_responses=False)
        return cls(settings=settings, engine=engine, db_factory=db_factory, redis=redis)

    async def close(self) -> None:
        await self.engine.dispose()
        await self.redis.aclose()
