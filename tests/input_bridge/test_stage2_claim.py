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


async def _try_claim(db_factory, ikey: str, sid: str, owner: str) -> bool:
    """Try to insert a #tentative lease. Returns True if claimed."""
    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
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
                    RETURNING idempotency_key
                    """
                ),
                {"etype": _TENTATIVE, "ikey": ikey, "sid": sid, "lease": LEASE, "owner": owner},
            )
            row = result.fetchone()
    return row is not None


async def _try_reclaim(db_factory, ikey: str, owner: str) -> bool:
    """Try to reclaim expired lease. Returns True if reclaimed."""
    async with db_factory() as db:
        async with db.begin():
            result = await db.execute(
                text(
                    """
                    UPDATE idempotency_keys
                    SET claim_expires_at = now() + (:lease * interval '1 second'),
                        claim_owner = :owner
                    WHERE envelope_type = :etype
                      AND idempotency_key = :ikey
                      AND claim_expires_at <= now()
                    RETURNING idempotency_key
                    """
                ),
                {"etype": _TENTATIVE, "ikey": ikey, "lease": LEASE, "owner": owner},
            )
            row = result.fetchone()
    return row is not None


async def _has_live_lease_for_other(db_factory, ikey: str, owner: str) -> bool:
    """Check if a live lease exists owned by someone other than owner."""
    async with db_factory() as db:
        result = await db.execute(
            text(
                """
                SELECT 1 FROM idempotency_keys
                WHERE envelope_type = :etype
                  AND idempotency_key = :ikey
                  AND claim_expires_at > now()
                  AND claim_owner != :owner
                """
            ),
            {"etype": _TENTATIVE, "ikey": ikey, "owner": owner},
        )
        return result.fetchone() is not None


async def _insert_committed(db_factory, ikey: str, sid: str, outcome: str = "delivered") -> None:
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
                         :outcome)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"etype": _COMMITTED, "ikey": ikey, "sid": sid, "outcome": outcome},
            )


async def _expire_lease(db_factory, ikey: str) -> None:
    """Manually expire the tentative lease for testing."""
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


@pytest.mark.asyncio
async def test_first_handler_acquires_tentative_lease(db_factory_fixture):
    """First handler acquires #tentative lease (INSERT succeeds)."""
    sid = await _seed_session(db_factory_fixture)
    ikey = str(uuid.uuid4())
    owner = str(uuid.uuid4())

    claimed = await _try_claim(db_factory_fixture, ikey, sid, owner)
    assert claimed is True


@pytest.mark.asyncio
async def test_concurrent_handler_with_live_lease_is_deferred(db_factory_fixture):
    """Concurrent handler with live lease returns 'deferred' (sees live lease)."""
    sid = await _seed_session(db_factory_fixture)
    ikey = str(uuid.uuid4())
    owner1 = str(uuid.uuid4())
    owner2 = str(uuid.uuid4())

    # First handler claims
    claimed = await _try_claim(db_factory_fixture, ikey, sid, owner1)
    assert claimed is True

    # Second handler's INSERT fails (conflict)
    second_claimed = await _try_claim(db_factory_fixture, ikey, sid, owner2)
    assert second_claimed is False

    # Second handler checks: live lease not owned by it
    other_has_live = await _has_live_lease_for_other(db_factory_fixture, ikey, owner2)
    assert other_has_live is True  # → should return "deferred"


@pytest.mark.asyncio
async def test_handler_can_reclaim_after_lease_expires(db_factory_fixture):
    """Handler after lease expires can reclaim."""
    sid = await _seed_session(db_factory_fixture)
    ikey = str(uuid.uuid4())
    owner1 = str(uuid.uuid4())
    owner2 = str(uuid.uuid4())

    # First handler claims
    claimed = await _try_claim(db_factory_fixture, ikey, sid, owner1)
    assert claimed is True

    # Expire the lease manually
    await _expire_lease(db_factory_fixture, ikey)

    # Second handler reclaims
    reclaimed = await _try_reclaim(db_factory_fixture, ikey, owner2)
    assert reclaimed is True


@pytest.mark.asyncio
async def test_reclaim_after_committed_is_skip_path(db_factory_fixture):
    """Reclaim path after #committed exists returns 'skipped_replay'."""
    sid = await _seed_session(db_factory_fixture)
    ikey = str(uuid.uuid4())
    owner1 = str(uuid.uuid4())
    owner2 = str(uuid.uuid4())

    # First handler claims
    claimed = await _try_claim(db_factory_fixture, ikey, sid, owner1)
    assert claimed is True

    # #committed written (delivery completed)
    await _insert_committed(db_factory_fixture, ikey, sid)

    # Expire the tentative lease
    await _expire_lease(db_factory_fixture, ikey)

    # Second handler reclaims
    reclaimed = await _try_reclaim(db_factory_fixture, ikey, owner2)
    assert reclaimed is True

    # Now check if #committed exists — it does → skip path
    async with db_factory_fixture() as db:
        result = await db.execute(
            text(
                "SELECT outcome FROM idempotency_keys "
                "WHERE envelope_type = :etype AND idempotency_key = :ikey"
            ),
            {"etype": _COMMITTED, "ikey": ikey},
        )
        row = result.fetchone()

    assert row is not None
    assert row[0] == "delivered"  # should return "skipped_replay" in full flow
