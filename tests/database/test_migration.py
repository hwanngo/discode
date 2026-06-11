from __future__ import annotations

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command
from discode.db.engine import make_engine, make_session_factory


@pytest.fixture(scope="module")
def migrated_db_url(db_url: str):
    """Run alembic upgrade head once for the module, then yield."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")
    yield db_url
    # Tear down: downgrade then re-upgrade so other test modules see a clean schema
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_upgrade_head_succeeds(migrated_db_url: str) -> None:
    """alembic upgrade head from base succeeds (creates all tables)."""
    # If we got here without exception, upgrade succeeded
    assert migrated_db_url is not None


@pytest.mark.asyncio
async def test_tables_exist_after_upgrade(migrated_db_url: str) -> None:
    """Core tables are present after upgrade head."""
    engine = make_engine(migrated_db_url)
    try:
        factory = make_session_factory(engine)
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                )
            )
            tables = {row[0] for row in result}
        expected = {
            "guilds",
            "guild_settings",
            "users",
            "runner_hosts",
            "allowed_roots",
            "sessions",
            "session_members",
            "events",
            "idempotency_keys",
            "redis_outbox",
        }
        assert expected.issubset(tables)
    finally:
        await engine.dispose()


def test_revision_ids_present(db_url: str) -> None:
    """Only the initial revision exists in alembic history."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")

    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(cfg)
    revisions = [rev.revision for rev in script.walk_revisions()]
    assert revisions == ["0001_initial"], f"expected only the squashed revision, got {revisions}"


def test_downgrade_base_succeeds(db_url: str) -> None:
    """alembic downgrade base from head succeeds (drops all tables cleanly)."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")
    # Should not raise
    command.downgrade(cfg, "base")
    # Re-upgrade so other tests can still use the DB
    command.upgrade(cfg, "head")


@pytest.mark.asyncio
async def test_new_tables_exist_after_upgrade(migrated_db_url: str) -> None:
    """guild_tool_channel_map and guild_admin_role_policy tables exist after upgrade head."""
    engine = make_engine(migrated_db_url)
    try:
        factory = make_session_factory(engine)
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                )
            )
            tables = {row[0] for row in result}
        assert "guild_tool_channel_map" in tables
        assert "guild_admin_role_policy" in tables
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_guild_tool_channel_map_unique_constraint(migrated_db_url: str) -> None:
    """guild_tool_channel_map enforces unique (guild_id, tool)."""
    engine = make_engine(migrated_db_url)
    try:
        factory = make_session_factory(engine)
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO guild_tool_channel_map (guild_id, tool, channel_id, updated_by) "
                    "VALUES ('guild-uc-test', 'claude', '111', 'user1')"
                )
            )
            await session.commit()
        # Second insert with same (guild_id, tool) should fail
        async with factory() as session:
            with pytest.raises(IntegrityError):
                await session.execute(
                    text(
                        "INSERT INTO guild_tool_channel_map"
                        " (guild_id, tool, channel_id, updated_by) "
                        "VALUES ('guild-uc-test', 'claude', '222', 'user2')"
                    )
                )
                await session.commit()
    finally:
        await engine.dispose()
