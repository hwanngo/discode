from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from discode.db.engine import make_engine, make_session_factory
from discode.db.models import Guild, RunnerHost, User
from discode.db.models import Session as SessionModel

_TENTATIVE = "runner.send_input.v1#tentative"
_COMMITTED = "runner.send_input.v1#committed"
LEASE = 30


@pytest_asyncio.fixture
async def db_factory_fixture(migrated_db_url: str):
    engine = make_engine(migrated_db_url)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


async def _seed_session(db_factory) -> str:  # type: ignore[no-untyped-def]
    guild_id = f"guild-{uuid.uuid4().hex[:8]}"
    user_id = f"user-{uuid.uuid4().hex[:8]}"
    host_id = f"host-{uuid.uuid4().hex[:8]}"
    session_id = str(uuid.uuid4())
    session_uuid = uuid.UUID(session_id)

    async with db_factory() as db:
        db.add(Guild(id=guild_id, name="Test Guild"))
        db.add(User(id=user_id, username="testuser"))
        db.add(RunnerHost(id=host_id, status="online"))
        db.add(
            SessionModel(
                id=session_uuid,
                guild_id=guild_id,
                owner_id=user_id,
                tool="claude",
                name="test",
                cwd="/home/user",
                cwd_realpath="/home/user",
                parent_channel_id="ch-001",
                thread_id=f"t-{uuid.uuid4().hex[:8]}",
                host_id=host_id,
                hook_secret_hash="hash",
                status="running",
            )
        )
        await db.commit()

    return session_id


async def _insert_tentative(db_factory, ikey: str, sid: str, owner: str) -> None:
    """Insert a fresh #tentative row with a live lease."""
    async with db_factory() as db:
        async with db.begin():
            await db.execute(
                text(
                    """
                    INSERT INTO idempotency_keys
                        (envelope_type, idempotency_key, session_id,
                         expires_at, claim_expires_at, claim_owner)
                    VALUES
                        (:etype, :ikey, :sid,
                         now() + interval '30 days',
                         now() + (:lease * interval '1 second'),
                         :owner)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"etype": _TENTATIVE, "ikey": ikey, "sid": sid, "lease": LEASE, "owner": owner},
            )


async def _expire_lease(db_factory, ikey: str) -> None:
    """Manually expire the tentative lease."""
    async with db_factory() as db:
        async with db.begin():
            await db.execute(
                text(
                    """
                    UPDATE idempotency_keys
                    SET claim_expires_at = now() - interval '1 second'
                    WHERE envelope_type = :etype AND idempotency_key = :ikey
                    """
                ),
                {"etype": _TENTATIVE, "ikey": ikey},
            )


async def _insert_committed(db_factory, ikey: str, sid: str) -> None:
    """Insert a #committed row."""
    async with db_factory() as db:
        async with db.begin():
            await db.execute(
                text(
                    """
                    INSERT INTO idempotency_keys
                        (envelope_type, idempotency_key, session_id,
                         expires_at, outcome)
                    VALUES
                        (:etype, :ikey, :sid,
                         now() + interval '30 days',
                         'delivered')
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"etype": _COMMITTED, "ikey": ikey, "sid": sid},
            )


async def _do_stage25_refresh(db_factory, ikey: str, owner: str) -> bool:
    """Execute Stage 2.5 refresh. Returns True if refresh succeeded (row affected)."""
    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                text(
                    """
                    UPDATE idempotency_keys
                    SET claim_expires_at = now() + (:lease * interval '1 second')
                    WHERE envelope_type = :etype
                      AND idempotency_key = :ikey
                      AND claim_owner = :owner
                      AND claim_expires_at > now()
                      AND NOT EXISTS (
                          SELECT 1 FROM idempotency_keys
                          WHERE envelope_type = :committed_etype
                            AND idempotency_key = :ikey
                      )
                    RETURNING idempotency_key
                    """
                ),
                {
                    "etype": _TENTATIVE,
                    "committed_etype": _COMMITTED,
                    "ikey": ikey,
                    "owner": owner,
                    "lease": LEASE,
                },
            )
            row = result.fetchone()
    return row is not None


@pytest.mark.asyncio
async def test_stage25_succeeds_with_valid_lease_no_committed(db_factory_fixture):
    """Stage 2.5 succeeds when lease is valid and no #committed exists."""
    sid = await _seed_session(db_factory_fixture)
    ikey = str(uuid.uuid4())
    owner = str(uuid.uuid4())

    await _insert_tentative(db_factory_fixture, ikey, sid, owner)

    success = await _do_stage25_refresh(db_factory_fixture, ikey, owner)
    assert success is True


@pytest.mark.asyncio
async def test_stage25_fails_when_lease_expired(db_factory_fixture):
    """Stage 2.5 fails (returns 0 rows) when lease is expired."""
    sid = await _seed_session(db_factory_fixture)
    ikey = str(uuid.uuid4())
    owner = str(uuid.uuid4())

    await _insert_tentative(db_factory_fixture, ikey, sid, owner)
    await _expire_lease(db_factory_fixture, ikey)

    success = await _do_stage25_refresh(db_factory_fixture, ikey, owner)
    assert success is False


@pytest.mark.asyncio
async def test_stage25_fails_when_committed_exists(db_factory_fixture):
    """Stage 2.5 fails (returns 0 rows) when #committed already exists."""
    sid = await _seed_session(db_factory_fixture)
    ikey = str(uuid.uuid4())
    owner = str(uuid.uuid4())

    await _insert_tentative(db_factory_fixture, ikey, sid, owner)
    await _insert_committed(db_factory_fixture, ikey, sid)

    success = await _do_stage25_refresh(db_factory_fixture, ikey, owner)
    assert success is False
