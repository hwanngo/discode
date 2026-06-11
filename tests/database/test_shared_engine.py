from __future__ import annotations

from discode.db.engine import get_shared_engine, get_shared_session_factory

_URL = "postgresql+asyncpg://u:p@localhost:5432/db"
_URL2 = "postgresql+asyncpg://u:p@localhost:5432/other"


def test_get_shared_engine_caches_per_url() -> None:
    """Repeated calls for the same URL return the same engine instance (pooled)."""
    a = get_shared_engine(_URL)
    b = get_shared_engine(_URL)
    assert a is b


def test_get_shared_engine_distinct_per_url() -> None:
    """Different URLs get distinct cached engines."""
    a = get_shared_engine(_URL)
    c = get_shared_engine(_URL2)
    assert a is not c


def test_shared_engine_has_pool_pre_ping() -> None:
    engine = get_shared_engine(_URL)
    assert engine.pool._pre_ping is True  # type: ignore[attr-defined]


def test_get_shared_session_factory_bound_to_shared_engine() -> None:
    """The shared factory is bound to the shared engine for the same URL."""
    engine = get_shared_engine(_URL)
    factory = get_shared_session_factory(_URL)
    assert factory.kw["bind"] is engine
