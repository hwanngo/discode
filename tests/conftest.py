from __future__ import annotations

import sys
from pathlib import Path

# Ensure the worktree's own src/ takes precedence over the editable install
# from the parent project. The editable .pth adds the parent project's src
# to sys.path at site-packages load time; inserting the worktree src at
# position 0 overrides those stale paths.
_WORKTREE_SRC = str(Path(__file__).parent.parent / "src")
if _WORKTREE_SRC not in sys.path:
    sys.path.insert(0, _WORKTREE_SRC)

# Evict any discode submodules that may already be cached from the editable
# install pointing at the parent project. After eviction the next import
# resolves against the worktree src we just prepended.
_STALE = [k for k in list(sys.modules) if k.startswith("discode")]
for _k in _STALE:
    del sys.modules[_k]

import dataclasses
import uuid

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from alembic.config import Config
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from alembic import command
from discode.db.engine import make_engine, make_session_factory


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture(scope="session")
def redis_container():
    with RedisContainer("redis:7") as r:
        yield r


@pytest.fixture(scope="session")
def db_url(postgres_container):
    url = postgres_container.get_connection_url()
    # Replace psycopg2 driver with asyncpg
    return url.replace("psycopg2", "asyncpg").replace("postgresql://", "postgresql+asyncpg://")


@pytest.fixture(scope="session")
def redis_url(redis_container):
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def redis_client(redis_container):
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client = aioredis.Redis(host=host, port=int(port))
    yield client
    await client.aclose()


@pytest.fixture(scope="session")
def migrated_db_url(db_url: str) -> str:
    """Run all migrations and return the db_url."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")
    return db_url


@pytest.fixture
async def db_session(migrated_db_url: str):
    """Provide a fresh async DB session per test (auto-rollback)."""
    engine = make_engine(migrated_db_url)
    factory = make_session_factory(engine)
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Shared guild / session fixtures (used by bot + runner tests)
# ---------------------------------------------------------------------------

_SHARED_ADMIN_ROLE_ID = "888"
_SHARED_ADMIN_ROLE_NAME = "admin"


@dataclasses.dataclass
class GuildAdminCtx:
    guild_id: str
    admin_role_ids: list[str]
    admin_role_names: list[str]


@pytest_asyncio.fixture
async def guild_with_admin_role(db_session):
    """Seed a guild + admin-role policy; return a GuildAdminCtx."""
    from discode.db.models import Guild, GuildAdminRolePolicy

    guild_id = f"guild-{uuid.uuid4().hex[:8]}"
    db_session.add(Guild(id=guild_id, name="Test Guild"))
    db_session.add(
        GuildAdminRolePolicy(
            guild_id=guild_id,
            role_id=_SHARED_ADMIN_ROLE_ID,
            role_name_fallback=_SHARED_ADMIN_ROLE_NAME,
            updated_by="test",
        )
    )
    await db_session.flush()
    return GuildAdminCtx(
        guild_id=guild_id,
        admin_role_ids=[_SHARED_ADMIN_ROLE_ID],
        admin_role_names=[_SHARED_ADMIN_ROLE_NAME],
    )


def _make_session_model(guild_id: str, tool: str):
    from discode.db.models import Session

    sid = uuid.uuid4()
    return Session(
        id=sid,
        guild_id=guild_id,
        tool=tool,
        name=f"test-session-{sid.hex[:6]}",
        cwd="/tmp",
        cwd_realpath="/tmp",
        hook_secret_hash="fakehash",
        status="idle",
    )


@pytest_asyncio.fixture
async def seed_session_in_guild(db_session, guild_with_admin_role):
    """Insert a Session row with tool='claude' in the test guild."""
    sess = _make_session_model(guild_with_admin_role.guild_id, "claude")
    db_session.add(sess)
    await db_session.flush()
    return sess
