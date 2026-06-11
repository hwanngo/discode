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
    exec_result = MagicMock()
    exec_result.fetchone.return_value = row
    sess.execute.return_value = exec_result
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=sess)
    cm.__aexit__ = AsyncMock(return_value=None)
    db_factory.return_value = cm

    lookup = build_session_lookup(db_factory)
    assert await lookup("sid-9") == {"thread_id": "thr-9", "status": "running"}
