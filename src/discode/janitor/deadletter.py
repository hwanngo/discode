from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def scan_input_dead_letters(redis_client: object, db_factory: object) -> int:
    """Scan input:deadletter stream for dead-lettered input envelopes. Returns count found."""
    # Stub: dead letter scan is a best-effort notification
    return 0
