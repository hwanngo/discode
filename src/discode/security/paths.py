from __future__ import annotations

import os

# Directory names whose presence ANYWHERE in a canonicalized path denies access.
# These are high-value credential / secret / system locations. Because /root add
# lets members self-register roots, this denylist IS the containment control, so
# it errs toward breadth.
DENYLIST_SEGMENTS = frozenset(
    {
        # Credential / config dotfile dirs
        ".ssh",
        ".aws",
        ".gnupg",
        ".config",
        ".docker",
        ".kube",
        ".local",
        ".gcloud",
        ".azure",
        ".npm",
        # System dirs
        "proc",
        "sys",
        "dev",
        "etc",
        "root",
        "boot",
    }
)

# Exact filenames that are credential files and must never be targeted, even
# when they sit inside an otherwise-allowed directory.
DENYLIST_FILENAMES = frozenset(
    {
        ".netrc",
        ".npmrc",
        ".git-credentials",
        ".pgpass",
    }
)


def _is_denied_filename(name: str) -> bool:
    """Return True if *name* is a credential file we never allow targeting."""
    if name in DENYLIST_FILENAMES:
        return True
    # .env, .env.local, .env.production, etc.
    if name == ".env" or name.startswith(".env."):
        return True
    return False


def canonicalize(path: str) -> tuple[str, str | None]:
    """Return (realpath, deny_reason_or_None).

    deny_reason is set if:
      * any directory segment of realpath is in DENYLIST_SEGMENTS, OR
      * any segment is a denied credential file (.netrc, .env*, etc).

    Symlinks and ``..`` are resolved first via ``os.path.realpath`` so a symlink
    pointing into a denied location is still caught.
    """
    real = os.path.realpath(path)
    parts = real.split(os.sep)
    for segment in parts:
        if not segment:
            continue
        if segment in DENYLIST_SEGMENTS:
            return real, f"denied segment: {segment!r}"
        if _is_denied_filename(segment):
            return real, f"denied credential file: {segment!r}"
    return real, None
