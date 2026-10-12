"""Real PostgreSQL/Redis regressions for delivery serialization and recovery."""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock

import pytest

from discode.dispatcher.consumer import (
    DISPATCHER_DEADLETTERS,
    DISPATCHER_GROUP,
    DISPATCHER_STREAM,
    DispatcherConsumer,
)
from discode.dispatcher.handlers.terminal_notice import _claim_act_finalize
from tests.input_bridge.test_stage2_claim import _seed_session, db_factory_fixture  # noqa: F401


async def test_concurrent_deliveries_are_serialized(db_factory_fixture):  # noqa: F811
    factory = db_factory_fixture
    sid = await _seed_session(factory)
    key = str(uuid.uuid4())
    entered = asyncio.Event()
    release = asyncio.Event()
    sends = 0

    async def send() -> None:
        nonlocal sends
        sends += 1
        entered.set()
        await release.wait()

    async def deliver() -> None:
        await _claim_act_finalize(
            factory, etype="discord.system_notice.v1", ikey=key, session_id=sid, side_effect=send
        )

    first = asyncio.create_task(deliver())
    await asyncio.wait_for(entered.wait(), 5)
    second = asyncio.create_task(deliver())
    await asyncio.sleep(0.1)
    assert sends == 1
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 5)
    assert sends == 1


async def test_failed_delivery_rolls_back_and_can_retry(db_factory_fixture):  # noqa: F811
    factory = db_factory_fixture
    sid = await _seed_session(factory)
    key = str(uuid.uuid4())
    side_effect = AsyncMock(side_effect=RuntimeError("offline"))

    async def deliver() -> None:
        await _claim_act_finalize(
            factory,
            etype="discord.system_notice.v1",
            ikey=key,
            session_id=sid,
            side_effect=side_effect,
        )

    with pytest.raises(RuntimeError, match="offline"):
        await deliver()
    side_effect.side_effect = None
    await deliver()
    await deliver()
    assert side_effect.await_count == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_exhausted_delivery_retained_before_ack(redis_client):
    consumer = DispatcherConsumer(redis_client, "audit-delivery-test")
    await consumer.ensure_group()
    fields = {"envelope_type": "discord.system_notice.v1", "payload": "{}"}
    mid = await redis_client.xadd(DISPATCHER_STREAM, fields)
    await redis_client.xreadgroup(DISPATCHER_GROUP, "audit-delivery-test", {DISPATCHER_STREAM: ">"})
    await consumer.dead_letter(mid.decode(), fields)
    saved = await redis_client.hget(DISPATCHER_DEADLETTERS, mid)
    assert json.loads(saved) == fields
    assert not await redis_client.xpending_range(DISPATCHER_STREAM, DISPATCHER_GROUP, mid, mid, 1)
    await redis_client.hdel(DISPATCHER_DEADLETTERS, mid)
