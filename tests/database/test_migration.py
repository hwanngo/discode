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


def test_revision_ids_present(db_url: str, monkeypatch) -> None:
    """Historical baseline and forward revision remain explicit."""
    monkeypatch.setenv("DATABASE_URL", db_url)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")

    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(cfg)
    revisions = [rev.revision for rev in script.walk_revisions()]
    assert revisions == [
        "0004_tool_seed_fixes",
        "0003_turn_delivery_failed",
        "0002_durable_turns",
        "0001_initial",
    ]


@pytest.mark.asyncio
async def test_orm_models_match_migrations(migrated_db_url: str) -> None:
    """Alembic autogenerate finds no difference between the ORM and migrated schema."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from discode.db.models import Base

    engine = make_engine(migrated_db_url)
    try:
        async with engine.connect() as conn:
            diff = await conn.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(sync_conn), Base.metadata
                )
            )
    finally:
        await engine.dispose()
    assert diff == []


def test_downgrade_base_succeeds(db_url: str, monkeypatch) -> None:
    """alembic downgrade base from head succeeds (drops all tables cleanly)."""
    monkeypatch.setenv("DATABASE_URL", db_url)
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


def test_0004_repairs_stale_pi_and_disables_opencode(db_url: str, monkeypatch) -> None:
    """Audit 2026-10-12: databases seeded before 24df464 get the corrected pi row."""
    import asyncio

    monkeypatch.setenv("DATABASE_URL", db_url)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)

    async def run(*statements: str) -> list:
        engine = make_engine(db_url)
        try:
            async with engine.begin() as conn:
                return [(await conn.execute(text(sql))).all() for sql in statements]
        finally:
            await engine.dispose()

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0003_turn_delivery_failed")
    asyncio.run(
        run(
            "UPDATE tool_definitions SET display_name='Pi (Inflection)', "
            "resume_token_source='none', argv_resume_tokens='[]' WHERE name='pi' RETURNING name",
            "UPDATE tool_definitions SET enabled=true WHERE name='opencode' RETURNING name",
        )
    )
    command.upgrade(cfg, "head")
    pi, opencode = asyncio.run(
        run(
            "SELECT resume_token_source, argv_resume_tokens::text FROM tool_definitions "
            "WHERE name='pi'",
            "SELECT enabled FROM tool_definitions WHERE name='opencode'",
        )
    )
    assert pi[0][0] == "caller_uuid"
    assert "--session-id" in pi[0][1]
    assert opencode[0][0] is False
