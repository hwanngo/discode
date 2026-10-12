from unittest.mock import AsyncMock

from discode.queues.retention import trim_acknowledged


async def test_pending_and_unread_entries_survive_retention():
    redis = AsyncMock()
    redis.xinfo_groups.return_value = [
        {"name": "first", "last-delivered-id": "500-0"},
        {"name": "second", "last-delivered-id": "100-0"},
    ]
    redis.xpending_range.side_effect = [[{"message_id": "50-0"}], []]
    redis.xtrim.return_value = 3
    assert await trim_acknowledged(redis, "work", 24) == 3
    redis.xtrim.assert_awaited_once_with("work", minid="50-0", approximate=False)


async def test_no_consumers_never_trims():
    redis = AsyncMock()
    redis.xinfo_groups.return_value = []
    assert await trim_acknowledged(redis, "work", 24) == 0
    redis.xtrim.assert_not_awaited()
