# Frozen registry of valid session state transitions.
# Format: {from_state: frozenset of valid to_states}
STATE_TRANSITIONS: dict[str, frozenset[str]] = {
    "creating": frozenset({"running", "failed"}),
    "running": frozenset({"idle", "stopping", "failed", "orphaned"}),
    "idle": frozenset({"running", "archived", "stopping", "orphaned"}),
    "archived": frozenset({"resuming"}),
    "resuming": frozenset({"running", "orphaned", "failed"}),
    "stopping": frozenset({"stopped", "failed", "orphaned"}),
    "stopped": frozenset(),
    "failed": frozenset(),
    "orphaned": frozenset(),
}
