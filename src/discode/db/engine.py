from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def make_engine(database_url: str):  # type: ignore[no-untyped-def]
    """Create async SQLAlchemy engine with pool_pre_ping=True."""
    return create_async_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine) -> async_sessionmaker[AsyncSession]:  # type: ignore[no-untyped-def]
    """Return async_sessionmaker bound to engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


# Process-wide cache of engines keyed by database URL. A SQLAlchemy async engine
# owns a connection pool; creating one per request (as the bot previously did on
# every Discord event) defeats pooling and exhausts Postgres connection slots.
# These shared accessors create the engine lazily on first use (inside the running
# event loop) and reuse it for the process lifetime.
_shared_engines: dict[str, object] = {}
_shared_factories: dict[str, async_sessionmaker[AsyncSession]] = {}


def get_shared_engine(database_url: str):  # type: ignore[no-untyped-def]
    """Return a process-wide cached engine for *database_url* (pooled, never per-request)."""
    engine = _shared_engines.get(database_url)
    if engine is None:
        engine = make_engine(database_url)
        _shared_engines[database_url] = engine
    return engine


def get_shared_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    """Return a cached session factory bound to the shared engine for *database_url*."""
    factory = _shared_factories.get(database_url)
    if factory is None:
        factory = make_session_factory(get_shared_engine(database_url))
        _shared_factories[database_url] = factory
    return factory


async def dispose_shared_engines() -> None:
    """Dispose all cached shared engines (call on clean process shutdown)."""
    for engine in list(_shared_engines.values()):
        await engine.dispose()  # type: ignore[attr-defined]
    _shared_engines.clear()
    _shared_factories.clear()
