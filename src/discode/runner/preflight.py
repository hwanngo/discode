from __future__ import annotations


def check_proc_isolation(runner_pid: int, agent_uid: int) -> bool:
    """No supported isolation backend exists; never report a security guarantee."""
    return False
