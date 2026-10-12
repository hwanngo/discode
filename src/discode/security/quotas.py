from __future__ import annotations

import os


def get_quota(name: str, default: int) -> int:
    """Load a per-session/per-minute cap: process env, then ``.env`` via Settings, then default.

    Reading only ``os.environ`` silently ignored values set in ``.env``.
    """
    if name in os.environ:
        return int(os.environ[name])
    try:
        from discode.config import get_settings

        return int(getattr(get_settings(), name))
    except Exception:
        return default


def MAX_OUTPUT_BYTES_PER_SESSION_PER_MIN() -> int:  # noqa: N802
    return get_quota("MAX_OUTPUT_BYTES_PER_SESSION_PER_MIN", 262144)


def MAX_INPUT_MESSAGES_PER_SESSION_PER_MIN() -> int:  # noqa: N802
    return get_quota("MAX_INPUT_MESSAGES_PER_SESSION_PER_MIN", 20)


def MAX_INPUT_BYTES_PER_MESSAGE() -> int:  # noqa: N802
    return get_quota("MAX_INPUT_BYTES_PER_MESSAGE", 8192)


def MAX_INPUT_BYTES_PER_SESSION_PER_MIN() -> int:  # noqa: N802
    return get_quota("MAX_INPUT_BYTES_PER_SESSION_PER_MIN", 163840)
