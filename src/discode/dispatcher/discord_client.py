from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import random
from collections.abc import Callable

import aiohttp

from discode.security.execution_policy import ensure_outbound_allowed

logger = logging.getLogger(__name__)


class DiscordPermanentError(RuntimeError):
    """A non-429 4xx from Discord (deleted thread, missing access, bad request).

    Retrying the same request cannot succeed.
    """

    def __init__(self, status: int) -> None:
        super().__init__(f"Discord send_message failed: {status}")
        self.status = status


DISCORD_API_BASE = "https://discord.com/api/v10"


class DiscordRestClient:
    """Async wrapper for Discord REST.

    - 429: honors Retry-After (JSON body or header), bounded retries.
    - 5xx: bounded retries with exponential backoff + jitter.
    - 4xx (non-429): raises immediately, no retry.
    """

    def __init__(
        self,
        token: str,
        *,
        max_retries: int = 3,
        backoff_base_seconds: float = 0.5,
    ) -> None:
        self._auth = token if token.startswith("Bot ") else f"Bot {token}"
        self._session: aiohttp.ClientSession | None = None
        self._max_retries = max_retries
        self._backoff_base = backoff_base_seconds
        self._on_retry_after: Callable[[str, float], None] | None = (
            None  # set by main.py to feed RateLimiter
        )

    def __repr__(self) -> str:
        return "DiscordRestClient(auth=[REDACTED])"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._auth,
            "Content-Type": "application/json",
            "User-Agent": "DiscodeDispatcher/1.0",
        }

    def set_retry_after_callback(self, cb: Callable[[str, float], None]) -> None:
        """Register a callback `cb(channel_id: str, seconds: float)` that
        external rate limiters can use to learn about server-side cool-offs."""
        self._on_retry_after = cb

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            headers=self._headers(), timeout=aiohttp.ClientTimeout(total=30)
        )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("DiscordRestClient not started; call await client.start() first")
        return self._session

    async def _extract_retry_after(self, resp: aiohttp.ClientResponse) -> float:
        # Prefer JSON body retry_after (decimal seconds, more precise) over header.
        try:
            body = await resp.json()
            if isinstance(body, dict) and "retry_after" in body:
                return float(body["retry_after"])
        except Exception:
            pass
        header = resp.headers.get("Retry-After")
        if header:
            try:
                return float(header)
            except ValueError:
                return 1.0
        return 1.0

    async def send_message(
        self,
        *,
        thread_id: str,
        content: str,
        nonce: str | None = None,
    ) -> str:
        url = f"{DISCORD_API_BASE}/channels/{thread_id}/messages"
        ensure_outbound_allowed()
        body: dict[str, object] = {"content": content, "allowed_mentions": {"parse": []}}
        if nonce is not None:
            # Discord rejects nonces > 25 chars (NONCE_TYPE_TOO_LONG).
            # Hash the full key, including the chunk suffix, to avoid collisions.
            body["nonce"] = (
                nonce
                if len(nonce) <= 25
                else hashlib.blake2b(nonce.encode(), digest_size=12).hexdigest()
            )
            # enforce_nonce makes Discord actually dedupe on the nonce; without
            # it the nonce is decorative and no idempotency is provided.
            body["enforce_nonce"] = True
        # Inline the retry loop here so we can return the message id from the
        # response inside the `async with`.
        attempt = 0
        unarchived = False
        while True:
            ensure_outbound_allowed()
            async with self._get_session().post(url, json=body) as resp:
                if resp.ok:
                    data = await resp.json()
                    return str(data["id"])
                if resp.status == 405 and not unarchived:
                    # Archived threads reject POST /messages with 405. Unarchive
                    # once and retry. If the unarchive itself fails or the
                    # second POST still 405s, fall through to the raise below.
                    unarchived = True
                    if await self._unarchive_thread(thread_id):
                        logger.info("send_message: unarchived thread=%s after 405", thread_id)
                        continue
                if resp.status == 429:
                    if attempt >= self._max_retries:
                        raise RuntimeError("Discord send_message rate-limit retry budget exhausted")
                    retry_after = await self._extract_retry_after(resp)
                    if not math.isfinite(retry_after) or retry_after < 0 or retry_after > 60:
                        raise RuntimeError("Discord Retry-After is outside the local retry budget")
                    attempt += 1
                    if self._on_retry_after is not None:
                        try:
                            self._on_retry_after(thread_id, retry_after)
                        except Exception:
                            logger.exception("retry_after callback failed")
                    logger.warning(
                        "send_message 429 thread=%s retry_after=%.2fs", thread_id, retry_after
                    )
                    await asyncio.sleep(retry_after)
                    continue
                if 500 <= resp.status < 600:
                    if attempt >= self._max_retries:
                        raise RuntimeError(
                            f"Discord send_message failed after retries: {resp.status}"
                        )
                    backoff = self._backoff_base * (2**attempt) + random.uniform(0, 0.1)
                    await asyncio.sleep(backoff)
                    attempt += 1
                    continue
                raise DiscordPermanentError(resp.status)

    async def _unarchive_thread(self, thread_id: str) -> bool:
        ensure_outbound_allowed()
        url = f"{DISCORD_API_BASE}/channels/{thread_id}"
        try:
            async with self._get_session().patch(url, json={"archived": False}) as resp:
                if resp.ok:
                    return True
                logger.warning("unarchive_thread failed: %d", resp.status)
                return False
        except Exception:
            logger.exception("unarchive_thread raised")
            return False

    async def archive_thread(self, *, thread_id: str) -> None:
        ensure_outbound_allowed()
        url = f"{DISCORD_API_BASE}/channels/{thread_id}"
        async with self._get_session().patch(url, json={"archived": True}) as resp:
            if not resp.ok:
                raise RuntimeError(f"Discord archive_thread failed: {resp.status}")

    async def trigger_typing(self, *, channel_id: str) -> None:
        """POST /channels/{id}/typing — typing indicator visible for ~10s.
        Discord auto-clears it after 10s or when the bot posts a message,
        whichever is sooner. Caller must refresh on a < 10s cadence to keep it on.
        """
        ensure_outbound_allowed()
        url = f"{DISCORD_API_BASE}/channels/{channel_id}/typing"
        async with self._get_session().post(url) as resp:
            if not resp.ok and resp.status != 429:
                # Don't raise on typing failure — it's best-effort UX.
                logger.warning("trigger_typing failed: %d", resp.status)
