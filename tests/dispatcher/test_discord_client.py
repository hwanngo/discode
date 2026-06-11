from __future__ import annotations

import pytest

from discode.dispatcher.discord_client import DISCORD_API_BASE, DiscordRestClient
from tests.dispatcher.conftest import FakeSession

BASE = DISCORD_API_BASE


@pytest.fixture
async def client() -> DiscordRestClient:  # type: ignore[misc]
    """A started client whose aiohttp session is replaced by a FakeSession."""
    c = DiscordRestClient(token="Bot test-token")
    c._session = FakeSession()  # type: ignore[assignment]
    yield c
    await c.close()


def _fake(client: DiscordRestClient) -> FakeSession:
    return client._session  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_send_message_returns_message_id(client: DiscordRestClient) -> None:
    _fake(client).set_response(
        "POST", f"{BASE}/channels/111/messages", payload={"id": "999", "content": "hello"}
    )
    msg_id = await client.send_message(thread_id="111", content="hello", nonce="nonce-1")
    assert msg_id == "999"


@pytest.mark.asyncio
async def test_send_message_passes_nonce(client: DiscordRestClient) -> None:
    _fake(client).set_response("POST", f"{BASE}/channels/222/messages", payload={"id": "888"})
    await client.send_message(thread_id="222", content="hi", nonce="my-nonce")
    assert _fake(client).json_for("POST", f"{BASE}/channels/222/messages")["nonce"] == "my-nonce"


@pytest.mark.asyncio
async def test_send_message_truncates_long_nonce(client: DiscordRestClient) -> None:
    """Discord rejects nonces > 25 chars (NONCE_TYPE_TOO_LONG). UUIDs are 36."""
    _fake(client).set_response("POST", f"{BASE}/channels/224/messages", payload={"id": "776"})
    long_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"  # 36 chars
    await client.send_message(thread_id="224", content="hi", nonce=long_uuid)

    sent_nonce = _fake(client).json_for("POST", f"{BASE}/channels/224/messages").get("nonce")
    assert sent_nonce is not None
    assert len(sent_nonce) <= 25
    assert sent_nonce == long_uuid[:25]


@pytest.mark.asyncio
async def test_send_message_omits_nonce_when_none(client: DiscordRestClient) -> None:
    _fake(client).set_response("POST", f"{BASE}/channels/223/messages", payload={"id": "777"})
    await client.send_message(thread_id="223", content="hi", nonce=None)
    assert "nonce" not in _fake(client).json_for("POST", f"{BASE}/channels/223/messages")


@pytest.mark.asyncio
async def test_send_message_raises_on_4xx(client: DiscordRestClient) -> None:
    _fake(client).set_response(
        "POST", f"{BASE}/channels/333/messages", status=404, payload={"message": "Unknown Channel"}
    )
    with pytest.raises(RuntimeError, match="404"):
        await client.send_message(thread_id="333", content="x", nonce="n")


@pytest.mark.asyncio
async def test_archive_thread_calls_patch_with_correct_body(client: DiscordRestClient) -> None:
    _fake(client).set_response(
        "PATCH", f"{BASE}/channels/444", payload={"id": "444", "archived": True}
    )
    await client.archive_thread(thread_id="444")
    assert _fake(client).json_for("PATCH", f"{BASE}/channels/444") == {"archived": True}


@pytest.mark.asyncio
async def test_archive_thread_raises_on_4xx(client: DiscordRestClient) -> None:
    _fake(client).set_response(
        "PATCH", f"{BASE}/channels/555", status=403, payload={"message": "Missing Permissions"}
    )
    with pytest.raises(RuntimeError, match="403"):
        await client.archive_thread(thread_id="555")


@pytest.mark.asyncio
async def test_bare_token_gets_bot_prefix() -> None:
    # Use a bare token (no "Bot " prefix)
    c = DiscordRestClient(token="myrawtoken")
    assert c._auth == "Bot myrawtoken"
    await c.close()  # nothing to close, just safe


@pytest.mark.asyncio
async def test_prefixed_token_not_doubled() -> None:
    c = DiscordRestClient(token="Bot alreadyprefixed")
    assert c._auth == "Bot alreadyprefixed"
    await c.close()


@pytest.mark.asyncio
async def test_repr_does_not_leak_token() -> None:
    c = DiscordRestClient(token="Bot supersecret")
    assert "supersecret" not in repr(c)
    assert "REDACTED" in repr(c)


@pytest.mark.asyncio
async def test_send_message_raises_when_not_started() -> None:
    c = DiscordRestClient(token="Bot test-token")
    with pytest.raises(RuntimeError, match="not started"):
        await c.send_message(thread_id="000", content="x")
