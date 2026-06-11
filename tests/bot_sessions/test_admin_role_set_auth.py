"""Pure-logic tests for setup_admin_role_set authorization (FIX 1).

The deny path returns before any DB write, so we can exercise it with a stubbed
session that yields "no policy configured".
"""

from __future__ import annotations

import types

import pytest

from discode.bot.setup_service import setup_admin_role_set


class _FakeScalars:
    def first(self):
        return None  # no policy => is_guild_admin_authorized fails closed


class _FakeResult:
    def scalars(self):
        return _FakeScalars()


def _fake_session():
    async def _execute(*_a, **_k):
        return _FakeResult()

    async def _flush():
        raise AssertionError("should not write when unauthorized")

    return types.SimpleNamespace(execute=_execute, flush=_flush)


@pytest.mark.asyncio
async def test_regular_member_cannot_self_promote_without_manage_guild():
    """No policy + not already_authorized => open DB check now DENIES."""
    result = await setup_admin_role_set(
        _fake_session(),
        guild_id="g1",
        role_id="r-admin",
        role_name="Admin",
        updated_by="member-1",
        member_roles=["r-admin"],
        member_role_names=["Admin"],
        already_authorized=False,
    )
    assert result.success is False
