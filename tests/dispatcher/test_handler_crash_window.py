"""Crash-window idempotency tests for terminal_notice and archive_thread handlers.

These handlers must use a claim-then-finalize pattern (like runner.saga_input):
claim the idempotency key with outcome=NULL, perform the Discord side effect,
and ONLY finalize (set outcome / set discord_thread_archived_at) after it
succeeds. If the Discord call fails, redelivery must legitimately RETRY rather
than skip.

The DB is faked in-memory at the session seam (no real Postgres). The fake
understands just enough of the SQL these handlers issue to model the
idempotency_keys table and the sessions archive timestamps across redeliveries.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from discode.dispatcher.handlers.archive_thread import handle_archive_thread
from discode.dispatcher.handlers.terminal_notice import handle_terminal_notice


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeDB:
    """A single async-session-like object backed by shared state.

    Models the subset of SQL the handlers use:
      - INSERT INTO idempotency_keys ... ON CONFLICT DO NOTHING RETURNING ...
        -> returns a row only when the (etype, ikey) was newly inserted.
      - UPDATE idempotency_keys SET outcome=... WHERE ... (finalize)
      - SELECT outcome FROM idempotency_keys WHERE ...
      - UPDATE sessions SET discord_thread_archived_at=now() ...
    """

    def __init__(self, store: dict):
        self._store = store

    def __call__(self):
        # db_factory() -> async context manager yielding self
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    def begin(self):
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        return txn

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        params = params or {}
        keys = self._store["ikeys"]
        sessions = self._store["sessions"]

        if sql.startswith("INSERT INTO idempotency_keys"):
            etype = self._etype_from_sql(sql, params)
            key = (etype, params["ikey"])
            if key in keys:
                return _FakeResult(None)  # ON CONFLICT DO NOTHING
            keys[key] = {"outcome": None}
            return _FakeResult(("row",))

        if sql.startswith("UPDATE idempotency_keys"):
            etype = self._etype_from_sql(sql, params)
            key = (etype, params["ikey"])
            if key in keys:
                keys[key]["outcome"] = params.get("outcome", "done")
                return _FakeResult(("row",))
            return _FakeResult(None)

        if sql.startswith("SELECT outcome FROM idempotency_keys"):
            etype = self._etype_from_sql(sql, params)
            key = (etype, params["ikey"])
            if key in keys:
                return _FakeResult((keys[key]["outcome"],))
            return _FakeResult(None)

        if sql.startswith("UPDATE sessions"):
            sid = params.get("sid")
            row = sessions.setdefault(sid, {})
            if "discord_thread_archived_at" in sql:
                row["discord_thread_archived_at"] = "now"
            if "thread_archive_requested_at" in sql:
                row["thread_archive_requested_at"] = "now"
            return _FakeResult(None)

        return _FakeResult(None)

    @staticmethod
    def _etype_from_sql(sql: str, params: dict) -> str:
        # The envelope_type is either a bound param (:etype) or a literal in the
        # VALUES / WHERE clause. Pull the literal so distinct stages don't alias.
        if "etype" in params:
            return params["etype"]
        import re

        m = re.search(r"'(discord\.[^']+)'", sql)
        return m.group(1) if m else "?"


def _new_store():
    return {"ikeys": {}, "sessions": {}}


# ---------------------------------------------------------------------------
# terminal_notice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_notice_retries_post_after_discord_failure() -> None:
    """If the Discord post raises, a second delivery must RETRY (not skip)."""
    store = _new_store()
    db = _FakeDB(store)
    envelope = {
        "session_id": "s1",
        "thread_id": "t1",
        "idempotency_key": "k1",
        "message": "done",
        "then_archive": False,
    }

    failing_send = AsyncMock(side_effect=RuntimeError("discord 503"))
    with pytest.raises(RuntimeError):
        await handle_terminal_notice(
            envelope,
            db_factory=db,
            send_to_discord=failing_send,
            archive_discord_thread=AsyncMock(),
        )

    # Second delivery: Discord now works — must be re-attempted.
    good_send = AsyncMock(return_value="msg-id")
    result = await handle_terminal_notice(
        envelope,
        db_factory=db,
        send_to_discord=good_send,
        archive_discord_thread=AsyncMock(),
    )

    assert result is True
    good_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminal_notice_skips_genuine_duplicate() -> None:
    """After a successful post, redelivery must NOT post again."""
    store = _new_store()
    db = _FakeDB(store)
    envelope = {
        "session_id": "s1",
        "thread_id": "t1",
        "idempotency_key": "k1",
        "message": "done",
        "then_archive": False,
    }

    send1 = AsyncMock(return_value="msg-id")
    await handle_terminal_notice(
        envelope, db_factory=db, send_to_discord=send1, archive_discord_thread=AsyncMock()
    )
    send1.assert_awaited_once()

    send2 = AsyncMock(return_value="msg-id")
    await handle_terminal_notice(
        envelope, db_factory=db, send_to_discord=send2, archive_discord_thread=AsyncMock()
    )
    send2.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_notice_happy_path_posts_and_archives() -> None:
    store = _new_store()
    db = _FakeDB(store)
    envelope = {
        "session_id": "s1",
        "thread_id": "t1",
        "idempotency_key": "k1",
        "message": "done",
        "then_archive": True,
    }
    send = AsyncMock(return_value="msg-id")
    archive = AsyncMock()
    result = await handle_terminal_notice(
        envelope, db_factory=db, send_to_discord=send, archive_discord_thread=archive
    )
    assert result is True
    send.assert_awaited_once()
    archive.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminal_notice_retries_archive_after_failure() -> None:
    """Stage 2 archive must retry on a second delivery if it failed first."""
    store = _new_store()
    db = _FakeDB(store)
    envelope = {
        "session_id": "s1",
        "thread_id": "t1",
        "idempotency_key": "k1",
        "message": "done",
        "then_archive": True,
    }

    send = AsyncMock(return_value="msg-id")
    failing_archive = AsyncMock(side_effect=RuntimeError("archive boom"))
    with pytest.raises(RuntimeError):
        await handle_terminal_notice(
            envelope, db_factory=db, send_to_discord=send, archive_discord_thread=failing_archive
        )

    # Post already finalized — should not re-post; archive must retry.
    send2 = AsyncMock(return_value="msg-id")
    good_archive = AsyncMock()
    await handle_terminal_notice(
        envelope, db_factory=db, send_to_discord=send2, archive_discord_thread=good_archive
    )
    send2.assert_not_awaited()
    good_archive.assert_awaited_once()


# ---------------------------------------------------------------------------
# archive_thread
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_thread_retries_after_discord_failure() -> None:
    """If archive_discord_thread raises, redelivery must retry, and the DB must
    NOT have been prematurely marked archived (scan_pending_archives must still
    find it)."""
    store = _new_store()
    db = _FakeDB(store)
    envelope = {"session_id": "s1", "thread_id": "t1", "idempotency_key": "k1"}

    failing = AsyncMock(side_effect=RuntimeError("archive 500"))
    with pytest.raises(RuntimeError):
        await handle_archive_thread(
            envelope, db_factory=db, archive_discord_thread=failing
        )

    # DB must NOT be marked archived after the failure.
    assert store["sessions"].get("s1", {}).get("discord_thread_archived_at") is None

    good = AsyncMock()
    result = await handle_archive_thread(
        envelope, db_factory=db, archive_discord_thread=good
    )
    assert result is True
    good.assert_awaited_once()
    # Now it should be marked archived.
    assert store["sessions"]["s1"]["discord_thread_archived_at"] == "now"


@pytest.mark.asyncio
async def test_archive_thread_skips_genuine_duplicate() -> None:
    store = _new_store()
    db = _FakeDB(store)
    envelope = {"session_id": "s1", "thread_id": "t1", "idempotency_key": "k1"}

    arch1 = AsyncMock()
    await handle_archive_thread(envelope, db_factory=db, archive_discord_thread=arch1)
    arch1.assert_awaited_once()

    arch2 = AsyncMock()
    await handle_archive_thread(envelope, db_factory=db, archive_discord_thread=arch2)
    arch2.assert_not_awaited()


@pytest.mark.asyncio
async def test_archive_thread_happy_path_marks_db() -> None:
    store = _new_store()
    db = _FakeDB(store)
    envelope = {"session_id": "s1", "thread_id": "t1", "idempotency_key": "k1"}
    arch = AsyncMock()
    result = await handle_archive_thread(envelope, db_factory=db, archive_discord_thread=arch)
    assert result is True
    arch.assert_awaited_once()
    assert store["sessions"]["s1"]["discord_thread_archived_at"] == "now"
