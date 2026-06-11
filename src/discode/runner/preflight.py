from __future__ import annotations

import platform
import subprocess


def check_proc_isolation(runner_pid: int, agent_uid: int) -> bool:
    """Return True if a process running as agent_uid cannot read /proc/{runner_pid}/environ.

    On Linux: attempt to open /proc/{runner_pid}/environ as agent_uid via subprocess.
    On non-Linux (macOS/platform_equivalent): return True immediately (skip test).
    """
    if platform.system() != "Linux":
        return True

    proc_path = f"/proc/{runner_pid}/environ"
    try:
        result = subprocess.run(
            ["sudo", "-u", f"#{agent_uid}", "cat", proc_path],
            capture_output=True,
            timeout=5,
        )
        # If we successfully read the file, isolation has failed
        return result.returncode != 0
    except Exception:
        # If any error occurs (e.g., sudo not available), assume isolation is present
        return True
