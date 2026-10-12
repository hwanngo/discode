"""Track typing against durable turn completion rather than unrelated Discord posts."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.db.models import InputTurn

if TYPE_CHECKING:
    from discode.bot.typing_tracker import TypingTracker

logger = logging.getLogger(__name__)


async def wait_for_turn(
    tracker: TypingTracker,
    factory: async_sessionmaker[AsyncSession],
    thread_id: str,
    key: str,
) -> None:
    try:
        async with asyncio.timeout(360):
            while True:
                async with factory() as db:
                    turn = await db.get(InputTurn, key)
                    if turn is None or turn.status in {
                        "delivered",
                        "uncertain",
                        "delivery_failed",
                        "aborted",
                    }:
                        return
                await asyncio.sleep(2)
    except TimeoutError:
        pass
    except Exception:
        logger.exception("Typing completion lookup failed")
    finally:
        await tracker.note_bot_message(thread_id)
