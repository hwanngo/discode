from __future__ import annotations

from discode.specs.audit_events import AUDIT_EVENT_TYPES


def test_all_audit_event_types_are_non_empty_strings() -> None:
    """All AUDIT_EVENT_TYPES are non-empty strings."""
    assert len(AUDIT_EVENT_TYPES) > 0
    for event_type in AUDIT_EVENT_TYPES:
        assert isinstance(event_type, str), f"Expected str, got {type(event_type)}"
        assert len(event_type) > 0, "Event type must not be empty"


def test_no_duplicate_entries() -> None:
    """No duplicate entries (frozenset guarantees this by nature, but we verify size)."""
    # frozenset inherently deduplicates; verify the count matches a list literal
    as_list = list(AUDIT_EVENT_TYPES)
    assert len(as_list) == len(set(as_list)), "Unexpected duplicates detected"


def test_known_required_event_type_present() -> None:
    """A known required event type is present."""
    assert "session.create.running" in AUDIT_EVENT_TYPES
    assert "session.stop.completed" in AUDIT_EVENT_TYPES
    assert "approval.created" in AUDIT_EVENT_TYPES
