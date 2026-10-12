from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from discode.runner.main import build_session_lookup


@pytest.mark.asyncio
async def test_session_lookup_returns_none_for_missing() -> None:
    db_factory = MagicMock()
    sess = AsyncMock()
    exec_result = MagicMock()
    exec_result.fetchone.return_value = None
    sess.execute.return_value = exec_result
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=sess)
    cm.__aexit__ = AsyncMock(return_value=None)
    db_factory.return_value = cm

    lookup = build_session_lookup(db_factory)
    assert await lookup("missing") is None


@pytest.mark.asyncio
async def test_session_lookup_returns_dict_for_existing() -> None:
    db_factory = MagicMock()
    sess = AsyncMock()
    row = MagicMock()
    row.thread_id = "thr-9"
    row.status = "running"
    row._mapping = {"thread_id": "thr-9", "status": "running"}
    exec_result = MagicMock()
    exec_result.fetchone.return_value = row
    sess.execute.return_value = exec_result
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=sess)
    cm.__aexit__ = AsyncMock(return_value=None)
    db_factory.return_value = cm

    lookup = build_session_lookup(db_factory)
    assert await lookup("00000000-0000-0000-0000-000000000009") == {
        "thread_id": "thr-9",
        "status": "running",
    }


async def test_session_lookup_is_scoped_to_current_runner():
    from discode.config import get_settings

    db = AsyncMock()
    result = MagicMock()
    result.fetchone.return_value = None
    db.execute.return_value = result
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    await build_session_lookup(factory)("00000000-0000-0000-0000-000000000009")
    statement, params = db.execute.call_args.args
    assert "host_id = :host" in str(statement)
    assert params["host"] == get_settings().RUNNER_HOST_ID
