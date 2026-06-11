"""Pure-logic tests for is_guild_admin_authorized (no DB / Docker required).

We stub the AsyncSession.execute result so the policy-decision branches can be
exercised directly.
"""

from __future__ import annotations

import types

import pytest

from discode.bot.policies.admin_auth import is_guild_admin_authorized


class _FakePolicy:
    def __init__(self, role_id=None, role_name_fallback=None):
        self.role_id = role_id
        self.role_name_fallback = role_name_fallback


class _FakeScalars:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return _FakeScalars(self._value)


def _fake_session(policy):
    async def _execute(*_a, **_k):
        return _FakeResult(policy)

    return types.SimpleNamespace(execute=_execute)


@pytest.mark.asyncio
async def test_no_policy_denies():
    """No policy row -> DENY (no fail-open bootstrap through this helper)."""
    result = await is_guild_admin_authorized(
        _fake_session(None), "g1", member_roles=["r1"], member_role_names=["Admin"]
    )
    assert result is False


@pytest.mark.asyncio
async def test_null_role_policy_denies():
    """Policy row exists but role_id/role_name both None -> DENY (configured=enforce)."""
    result = await is_guild_admin_authorized(
        _fake_session(_FakePolicy(role_id=None, role_name_fallback=None)),
        "g1",
        member_roles=["r1"],
        member_role_names=["Admin"],
    )
    assert result is False


@pytest.mark.asyncio
async def test_role_id_match_allows():
    result = await is_guild_admin_authorized(
        _fake_session(_FakePolicy(role_id="r-admin")),
        "g1",
        member_roles=["r-admin"],
    )
    assert result is True


@pytest.mark.asyncio
async def test_role_id_no_match_denies():
    result = await is_guild_admin_authorized(
        _fake_session(_FakePolicy(role_id="r-admin")),
        "g1",
        member_roles=["r-other"],
    )
    assert result is False


@pytest.mark.asyncio
async def test_role_name_fallback_match_allows():
    result = await is_guild_admin_authorized(
        _fake_session(_FakePolicy(role_name_fallback="Server Admin")),
        "g1",
        member_roles=[],
        member_role_names=["Server Admin"],
    )
    assert result is True
