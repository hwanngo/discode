from __future__ import annotations

from discode.specs.state_machine import STATE_TRANSITIONS

TERMINAL_STATES = {"stopped", "failed", "orphaned"}
ALL_STATES = set(STATE_TRANSITIONS.keys())
VALID_SESSION_STATUSES = {
    "creating",
    "running",
    "idle",
    "archived",
    "resuming",
    "stopping",
    "stopped",
    "failed",
    "orphaned",
}


def _reachable_terminals(start: str, visited: set[str] | None = None) -> set[str]:
    """DFS to find all terminal states reachable from start."""
    if visited is None:
        visited = set()
    if start in visited:
        return set()
    visited.add(start)
    if start in TERMINAL_STATES:
        return {start}
    result: set[str] = set()
    for next_state in STATE_TRANSITIONS.get(start, frozenset()):
        result |= _reachable_terminals(next_state, visited)
    return result


def test_every_state_has_reachable_terminal() -> None:
    """Every state in STATE_TRANSITIONS has at least one reachable terminal state."""
    for state in STATE_TRANSITIONS:
        terminals = _reachable_terminals(state)
        assert terminals, f"State '{state}' cannot reach any terminal state"


def test_terminal_states_have_no_outgoing_transitions() -> None:
    """'stopped', 'failed', 'orphaned' have no outgoing transitions (terminal states)."""
    for state in TERMINAL_STATES:
        assert state in STATE_TRANSITIONS, f"Terminal state '{state}' missing from registry"
        transitions = STATE_TRANSITIONS[state]
        assert len(transitions) == 0, (
            f"Terminal state '{state}' has outgoing transitions: {transitions}"
        )


def test_all_states_are_valid_session_status_values() -> None:
    """All states are valid session status values."""
    for state in STATE_TRANSITIONS:
        assert state in VALID_SESSION_STATUSES, (
            f"State '{state}' is not a valid session status value"
        )
    # Also check all target states
    for from_state, to_states in STATE_TRANSITIONS.items():
        for to_state in to_states:
            assert to_state in VALID_SESSION_STATUSES, (
                f"Target state '{to_state}' (from '{from_state}') is not a valid session status"
            )
