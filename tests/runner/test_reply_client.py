from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from discode.runner.reply_client import ReplyClient, ReplyError


async def _make_server(handler: web.AbstractRouteDef) -> TestServer:
    app = web.Application()
    app.router.add_routes([handler])
    server = TestServer(app)
    await server.start_server()
    return server


@pytest.mark.asyncio
async def test_post_reply_happy() -> None:
    received: dict = {}

    async def handler(request: web.Request) -> web.Response:
        received["auth"] = request.headers.get("Authorization", "")
        received["body"] = await request.json()
        return web.json_response({"message_id": "m1", "delivered": True})

    server = await _make_server(web.post("/v1/sessions/sid-1/reply", handler))
    base = f"http://127.0.0.1:{server.port}"

    client = ReplyClient(base_url=base, bearer="t")
    try:
        msg_id = await client.post_reply("sid-1", "hi", idempotency_key="k1")
        assert msg_id == "m1"
        assert received["auth"] == "Bearer t"
        assert received["body"] == {"text": "hi", "idempotency_key": "k1"}
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_post_reply_404_raises() -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"detail": "session_not_found_or_dead"}, status=404)

    server = await _make_server(web.post("/v1/sessions/sid-x/reply", handler))
    base = f"http://127.0.0.1:{server.port}"

    client = ReplyClient(base_url=base, bearer="t")
    try:
        with pytest.raises(ReplyError) as exc:
            await client.post_reply("sid-x", "hi", idempotency_key="k1")
        assert exc.value.status == 404
        assert "session_not_found_or_dead" in exc.value.detail
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_post_reply_503_raises() -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"detail": "reply pipeline not wired"}, status=503)

    server = await _make_server(web.post("/v1/sessions/sid/reply", handler))
    base = f"http://127.0.0.1:{server.port}"

    client = ReplyClient(base_url=base, bearer="t")
    try:
        with pytest.raises(ReplyError) as exc:
            await client.post_reply("sid", "hi", idempotency_key="k1")
        assert exc.value.status == 503
    finally:
        await client.close()
        await server.close()
