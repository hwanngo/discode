from __future__ import annotations

import aiohttp


class ReplyError(Exception):
    """Reply endpoint returned a non-200 status."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"reply failed: {status} {detail}")
        self.status = status
        self.detail = detail


class ReplyClient:
    """Client for the runner's POST /v1/sessions/{sid}/reply endpoint.

    Used by saga_input to deliver tool output back to Discord via the
    control API (which holds the rate-limited DiscordRestClient).
    """

    def __init__(self, base_url: str, bearer: str) -> None:
        self._base = base_url.rstrip("/")
        self._bearer = bearer
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self._bearer}"},
                timeout=aiohttp.ClientTimeout(total=60),
            )
        return self._session

    async def post_reply(
        self,
        session_id: str,
        text: str,
        *,
        idempotency_key: str,
        lifecycle_version: int | None = None,
    ) -> str:
        url = f"{self._base}/v1/sessions/{session_id}/reply"
        s = await self._get_session()
        body: dict[str, str | int] = {"text": text, "idempotency_key": idempotency_key}
        if lifecycle_version is not None:
            body["lifecycle_version"] = lifecycle_version
        async with s.post(url, json=body) as resp:
            if resp.status != 200:
                detail = await resp.text()
                raise ReplyError(resp.status, detail)
            data = await resp.json()
            return str(data["message_id"])

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
