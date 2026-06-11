from __future__ import annotations

import pytest

from discode.tools.errors import UnknownTool
from discode.tools.registry import get_adapter


@pytest.mark.asyncio
async def test_registry_returns_adapter_and_tool_def_for_enabled_seed(db_session):
    adapter, tool_def = await get_adapter(db_session, "claude")
    assert adapter.name == "claude"
    assert tool_def.name == "claude"
    assert tool_def.argv_prefix[0] == "claude"


@pytest.mark.asyncio
async def test_registry_raises_for_disabled_pi(db_session):
    with pytest.raises(UnknownTool):
        await get_adapter(db_session, "pi")


@pytest.mark.asyncio
async def test_registry_raises_for_unknown(db_session):
    with pytest.raises(UnknownTool):
        await get_adapter(db_session, "nonexistent")
