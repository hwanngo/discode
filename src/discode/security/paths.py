from __future__ import annotations

import os

# Directory names whose presence ANYWHERE in a canonicalized path denies access.
# These are high-value credential / secret locations. This protects workspace
# selection only; it cannot sandbox a spawned tool or prevent access to other
# host paths. trusted_host requires trusted actors and a disposable host.
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
    }
)

# System directories, denied only at the filesystem root (and macOS's /private
# aliases) so ordinary project folders such as ~/dev/app or ~/src/etc-tool work.
DENYLIST_ROOTS = (
    "/proc",
    "/sys",
    "/dev",
    "/etc",
    "/root",
    "/boot",
    "/private/etc",
    "/private/var/root",
    "/var/root",
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
      * realpath is (under) a system root in DENYLIST_ROOTS, OR
      * any directory segment of realpath is in DENYLIST_SEGMENTS, OR
      * any segment is a denied credential file (.netrc, .env*, etc).

    Symlinks and ``..`` are resolved first via ``os.path.realpath`` so a symlink
    pointing into a denied location is still caught.
    """
    real = os.path.realpath(path)
    for root in DENYLIST_ROOTS:
        if real == root or real.startswith(root + os.sep):
            return real, f"denied system path: {root!r}"
    parts = real.split(os.sep)
    for segment in parts:
        if not segment:
            continue
        if segment in DENYLIST_SEGMENTS:
            return real, f"denied segment: {segment!r}"
        if _is_denied_filename(segment):
            return real, f"denied credential file: {segment!r}"
    return real, None


def is_within_root(path: str, root: str) -> bool:
    """True if canonical ``path`` is ``root`` or inside it (``root`` may be ``/``)."""
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)
