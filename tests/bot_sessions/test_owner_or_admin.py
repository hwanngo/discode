"""Pure-logic tests for FIX 4: is_session_owner_or_admin.

owner-or-admin gates /stop /archive /resume /restart. A plain (invited) member
must NOT pass.
"""

from __future__ import annotations

import contextlib
import types
import uuid

import pytest

from discode.bot.session_service import is_session_owner_or_admin


def _fake_db_factory(owner_id):
    sess_row = types.SimpleNamespace(owner_id=owner_id)

    async def _get(_model, _pk):
        return sess_row

    fake = types.SimpleNamespace(get=_get)

    @contextlib.asynccontextmanager
    async def _ctx():
        yield fake

    return lambda: _ctx()


@pytest.mark.asyncio
async def test_owner_passes():
    sid = str(uuid.uuid4())
    ok = await is_session_owner_or_admin(
        _fake_db_factory(owner_id="owner-1"),
        session_id=sid,
        guild_id="g1",
        user_id="owner-1",
        member_roles=[],
        member_role_names=[],
    )
    assert ok is True


@pytest.mark.asyncio
async def test_invited_member_denied(monkeypatch):
    # admin check returns False; user is not the owner -> denied
    async def _fake_auth(db, *, guild_id, member_roles, member_role_names=None):
        return False

    import discode.bot.policies.admin_auth as auth_mod

    monkeypatch.setattr(auth_mod, "is_guild_admin_authorized", _fake_auth)

    sid = str(uuid.uuid4())
    ok = await is_session_owner_or_admin(
        _fake_db_factory(owner_id="owner-1"),
        session_id=sid,
        guild_id="g1",
        user_id="invited-operator-2",
        member_roles=["r-regular"],
        member_role_names=["Member"],
    )
    assert ok is False


@pytest.mark.asyncio
async def test_admin_passes(monkeypatch):
    async def _fake_auth(db, *, guild_id, member_roles, member_role_names=None):
        return True

    import discode.bot.policies.admin_auth as auth_mod

    monkeypatch.setattr(auth_mod, "is_guild_admin_authorized", _fake_auth)

    sid = str(uuid.uuid4())
    ok = await is_session_owner_or_admin(
        _fake_db_factory(owner_id="owner-1"),
        session_id=sid,
        guild_id="g1",
        user_id="admin-3",
        member_roles=["r-admin"],
        member_role_names=["Admin"],
    )
    assert ok is True
