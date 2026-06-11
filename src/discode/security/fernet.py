from __future__ import annotations

import os

from cryptography.fernet import Fernet, MultiFernet


def load_multifernet(key_csv: str) -> MultiFernet:
    """Parse comma-separated Fernet keys into a MultiFernet instance."""
    keys = [Fernet(k.strip().encode()) for k in key_csv.split(",")]
    return MultiFernet(keys)


def generate_hook_secret() -> bytes:
    """Return 32 cryptographically random bytes."""
    return os.urandom(32)
