"""FIX 3: resume (run_resume_job) and recover (recover_sessions) must
re-canonicalize the stored cwd and re-check it against current allowed roots
before rehydrating a session.

DB-GATED: these tests require the migrated Postgres test DB (testcontainers /
docker). They will run in CI. The pure-logic guard itself is also covered by
test_saga_create_rowcount.py and the revalidate_cwd unit test below, which run
without docker.
"""

from __future__ import annotations

import pytest

from discode.runner.saga_create import revalidate_cwd

# --------------------------------------------------------------------------
# Pure-logic: revalidate_cwd denylist rejection needs no allowed_roots / DB
# write, but it does issue a SELECT, so it is DB-gated. We still assert the
# denylist branch short-circuits before the query for a denied segment.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revalidate_cwd_denylist_short_circuits():
    """A cwd containing a denied segment is rejected before any DB query."""

    class _NoDB:
        async def execute(self, *a, **k):  # pragma: no cover - must not be called
            raise AssertionError("execute should not run for denied segment")

    realpath, reason = await revalidate_cwd(
        _NoDB(),
        cwd="/home/user/.ssh/keys",
        guild_id="g",
        owner_id="o",
        host_id="h",
    )
    assert realpath is None
    assert reason is not None
    assert ".ssh" in reason


# --------------------------------------------------------------------------
# DB-gated integration tests
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_skips_session_with_invalid_cwd(
    db_session, guild_with_admin_role
):
    """recover_sessions must refuse to rehydrate a session whose cwd no longer
    resolves under an allowed root (e.g. root revoked / symlink swap)."""
    import uuid

    from sqlalchemy import text

    from discode.db.models import Session as SessionModel
    from discode.runner.saga_resume import recover_sessions

    host_id = "host-recover-test"
    sid = uuid.uuid4()
    db_session.add(
        SessionModel(
            id=sid,
            guild_id=guild_with_admin_role.guild_id,
            tool="claude",
            name="recover-bad-cwd",
            cwd="/tmp/not-allowed",
            cwd_realpath="/tmp/not-allowed",
            hook_secret_hash="h",
            status="running",
            host_id=host_id,
            owner_id="owner-x",
        )
    )
    await db_session.commit()

    # No AllowedRoot rows for this host/guild -> revalidation must fail.
    from sqlalchemy.ext.asyncio import async_sessionmaker

    db_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    store: dict = {}
    recovered, failed = await recover_sessions(
        db_factory, host_id=host_id, session_store=store
    )

    assert str(sid) not in store
    assert failed >= 1
    # Session marked failed with cwd_invalid reason.
    row = (
        await db_session.execute(
            text("SELECT status, status_reason FROM sessions WHERE id=:s"),
            {"s": str(sid)},
        )
    ).fetchone()
    assert row[0] == "failed"
    assert row[1] == "cwd_invalid"


@pytest.mark.asyncio
async def test_resume_refuses_invalid_cwd(db_session, guild_with_admin_role):
    """run_resume_job must refuse a resuming session whose cwd is invalid."""
    import uuid

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from discode.db.models import Session as SessionModel
    from discode.runner.resume_runner import run_resume_job

    host_id = "host-resume-test"
    sid = uuid.uuid4()
    db_session.add(
        SessionModel(
            id=sid,
            guild_id=guild_with_admin_role.guild_id,
            tool="claude",
            name="resume-bad-cwd",
            cwd="/tmp/not-allowed",
            cwd_realpath="/tmp/not-allowed",
            hook_secret_hash="h",
            status="resuming",
            host_id=host_id,
            owner_id="owner-x",
        )
    )
    await db_session.commit()

    db_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    ok = await run_resume_job(
        {
            "session_id": str(sid),
            "guild_id": guild_with_admin_role.guild_id,
            "thread_id": "t",
        },
        host_id=host_id,
        session_store={},
        db_factory=db_factory,
    )
    assert ok is False
    row = (
        await db_session.execute(
            text("SELECT status, status_reason FROM sessions WHERE id=:s"),
            {"s": str(sid)},
        )
    ).fetchone()
    assert row[0] == "failed"
    assert row[1] == "cwd_invalid"
