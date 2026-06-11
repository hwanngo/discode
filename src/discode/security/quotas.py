from __future__ import annotations

import os


def get_quota(name: str, default: int) -> int:
    """Load a quota value from environment variable. Used for per-session/per-minute caps."""
    return int(os.environ.get(name, str(default)))


def MAX_OUTPUT_BYTES_PER_SESSION_PER_MIN() -> int:  # noqa: N802
    return get_quota("MAX_OUTPUT_BYTES_PER_SESSION_PER_MIN", 262144)


def MAX_INPUT_MESSAGES_PER_SESSION_PER_MIN() -> int:  # noqa: N802
    return get_quota("MAX_INPUT_MESSAGES_PER_SESSION_PER_MIN", 20)


def MAX_INPUT_BYTES_PER_MESSAGE() -> int:  # noqa: N802
    return get_quota("MAX_INPUT_BYTES_PER_MESSAGE", 8192)


def MAX_INPUT_BYTES_PER_SESSION_PER_MIN() -> int:  # noqa: N802
    return get_quota("MAX_INPUT_BYTES_PER_SESSION_PER_MIN", 163840)
