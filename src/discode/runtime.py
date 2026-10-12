from __future__ import annotations

import asyncio
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from typing import cast

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discode.config import Settings, get_settings
from discode.db.engine import make_engine, make_session_factory
from discode.metrics import start_metrics_server


@dataclass
class AppRuntime:
    settings: Settings
    engine: AsyncEngine
    db_factory: async_sessionmaker[AsyncSession]
    redis: Redis

    metrics_server: ThreadingHTTPServer | None = None

    def start_metrics(self, bind: str) -> None:
        self.metrics_server = cast(ThreadingHTTPServer, start_metrics_server(bind))

    @classmethod
    def from_env(cls) -> AppRuntime:
        settings = get_settings()
        return cls.from_settings(settings)

    @classmethod
    def from_settings(cls, settings: Settings) -> AppRuntime:
        engine = make_engine(
            settings.DATABASE_URL,
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
        )
        db_factory = make_session_factory(engine)
        redis = Redis.from_url(settings.REDIS_URL, decode_responses=False)
        return cls(settings=settings, engine=engine, db_factory=db_factory, redis=redis)

    async def close(self) -> None:
        if self.metrics_server is not None:
            await asyncio.to_thread(self.metrics_server.shutdown)
            self.metrics_server.server_close()
        await self.engine.dispose()
        await self.redis.aclose()
