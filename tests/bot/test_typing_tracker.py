from __future__ import annotations

import asyncio

import pytest

from discode.bot.typing_tracker import TypingTracker


def _stub_trigger_factory():
    """Returns (factory, calls). factory(thread_id) -> async no-op trigger.
    `calls` is a list of thread_ids that were triggered."""
    calls: list[str] = []

    async def _no_op() -> None:
        pass

    def factory(thread_id: str):
        calls.append(thread_id)
        return _no_op

    return factory, calls


@pytest.mark.asyncio
async def test_first_user_message_starts_indicator() -> None:
    factory, calls = _stub_trigger_factory()
    tracker = TypingTracker(trigger_factory=factory)
    try:
        await tracker.note_user_message("thr-1")
        assert tracker.pending("thr-1") == 1
        assert tracker.is_running("thr-1") is True
        assert calls == ["thr-1"]
    finally:
        await tracker.shutdown()


@pytest.mark.asyncio
async def test_second_user_message_only_increments_counter() -> None:
    factory, calls = _stub_trigger_factory()
    tracker = TypingTracker(trigger_factory=factory)
    try:
        await tracker.note_user_message("thr-2")
        await tracker.note_user_message("thr-2")
        assert tracker.pending("thr-2") == 2
        assert tracker.is_running("thr-2") is True
        # trigger_factory should have been called exactly once — only one task.
        assert calls == ["thr-2"]
    finally:
        await tracker.shutdown()


@pytest.mark.asyncio
async def test_bot_reply_decrements_and_keeps_running() -> None:
    factory, _ = _stub_trigger_factory()
    tracker = TypingTracker(trigger_factory=factory)
    try:
        await tracker.note_user_message("thr-3")
        await tracker.note_user_message("thr-3")
        await tracker.note_user_message("thr-3")
        assert tracker.pending("thr-3") == 3

        await tracker.note_bot_message("thr-3")
        assert tracker.pending("thr-3") == 2
        assert tracker.is_running("thr-3") is True

        await tracker.note_bot_message("thr-3")
        assert tracker.pending("thr-3") == 1
        assert tracker.is_running("thr-3") is True
    finally:
        await tracker.shutdown()


@pytest.mark.asyncio
async def test_bot_reply_at_zero_stops_indicator() -> None:
    factory, _ = _stub_trigger_factory()
    tracker = TypingTracker(trigger_factory=factory)
    try:
        await tracker.note_user_message("thr-4")
        await tracker.note_bot_message("thr-4")
        assert tracker.pending("thr-4") == 0
        assert tracker.is_running("thr-4") is False
    finally:
        await tracker.shutdown()


@pytest.mark.asyncio
async def test_bot_message_with_zero_counter_is_noop() -> None:
    """If the bot was restarted between user msg and bot reply, pending=0
    when the reply lands. Tracker must not underflow or raise."""
    factory, _ = _stub_trigger_factory()
    tracker = TypingTracker(trigger_factory=factory)
    try:
        await tracker.note_bot_message("thr-5")  # nothing to decrement
        assert tracker.pending("thr-5") == 0
        assert tracker.is_running("thr-5") is False
        # And a subsequent user message should still start cleanly:
        await tracker.note_user_message("thr-5")
        assert tracker.pending("thr-5") == 1
        assert tracker.is_running("thr-5") is True
    finally:
        await tracker.shutdown()


@pytest.mark.asyncio
async def test_safety_timeout_zeros_counter() -> None:
    """When max_duration elapses with no decrement, the tracker zeroes the
    counter so the next user message starts fresh."""
    factory, _ = _stub_trigger_factory()
    tracker = TypingTracker(
        trigger_factory=factory,
        refresh_interval=0.05,
        max_duration=0.2,  # 200ms for the test
    )
    try:
        await tracker.note_user_message("thr-6")
        assert tracker.pending("thr-6") == 1
        # Wait for the safety timeout to fire.
        await asyncio.sleep(0.4)
        assert tracker.pending("thr-6") == 0
        assert tracker.is_running("thr-6") is False
        # Subsequent user message must start a fresh task.
        await tracker.note_user_message("thr-6")
        assert tracker.pending("thr-6") == 1
        assert tracker.is_running("thr-6") is True
    finally:
        await tracker.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_all_tasks() -> None:
    factory, _ = _stub_trigger_factory()
    tracker = TypingTracker(trigger_factory=factory)
    await tracker.note_user_message("thr-a")
    await tracker.note_user_message("thr-b")
    await tracker.note_user_message("thr-b")
    assert tracker.is_running("thr-a") is True
    assert tracker.is_running("thr-b") is True

    await tracker.shutdown()

    assert tracker.is_running("thr-a") is False
    assert tracker.is_running("thr-b") is False
    assert tracker.pending("thr-a") == 0
    assert tracker.pending("thr-b") == 0
