# Frozen registry of valid audit event types.
AUDIT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "session.create.requested",
        "session.create.running",
        "session.create.failed",
        "session.stop.requested",
        "session.stop.completed",
        "session.archive.requested",
        "session.archive.completed",
        "session.resume.requested",
        "session.resume.completed",
        "session.resume.failed",
        "session.restart.requested",
        "session.invite.requested",
        "transcript.quota_exceeded",
        "worktree.created",
        "worktree.deleted",
        "worktree.orphan_cleaned",
        "approval.created",
        "approval.responded",
        "approval.expired",
        "approval.auto_approved",
    }
)


def validate_audit_event_type(type_str: str) -> None:
    """Raise ValueError if type_str is not a known audit event type."""
    if type_str not in AUDIT_EVENT_TYPES:
        raise ValueError(f"unknown audit event type: {type_str!r}")
