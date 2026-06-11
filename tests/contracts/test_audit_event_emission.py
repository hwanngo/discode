from __future__ import annotations

import pytest

from discode.specs.audit_events import AUDIT_EVENT_TYPES, validate_audit_event_type


def test_validate_audit_event_type_accepts_known_type() -> None:
    validate_audit_event_type("session.create.requested")  # must not raise


def test_validate_audit_event_type_raises_for_unknown() -> None:
    with pytest.raises(ValueError, match="unknown audit event type"):
        validate_audit_event_type("not.a.real.event")


def test_session_create_requested_in_registry() -> None:
    assert "session.create.requested" in AUDIT_EVENT_TYPES


def test_emit_event_raises_for_unknown_type() -> None:
    """emit_event rejects unknown event types without a DB session."""
    import asyncio
    from unittest.mock import MagicMock

    from discode.db.models.event import emit_event

    mock_session = MagicMock()

    async def _run() -> None:
        with pytest.raises(ValueError, match="unknown audit event type"):
            await emit_event(mock_session, type="bogus.event.type")

    asyncio.run(_run())


def test_emit_event_adds_event_to_session() -> None:
    """emit_event calls session.add with an Event for a known type."""
    import asyncio
    from unittest.mock import MagicMock

    from discode.db.models.event import Event, emit_event

    mock_session = MagicMock()

    async def _run() -> None:
        event = await emit_event(
            mock_session,
            type="session.create.requested",
            guild_id="guild-1",
            actor_id="user-1",
            payload={"tool": "claude"},
        )
        assert isinstance(event, Event)
        mock_session.add.assert_called_once_with(event)
        assert event.type == "session.create.requested"
        assert event.guild_id == "guild-1"
        assert event.actor_id == "user-1"

    asyncio.run(_run())
