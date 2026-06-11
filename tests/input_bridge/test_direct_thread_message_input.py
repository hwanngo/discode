"""Tests for direct thread message input routing — Task 5.

Tests cover:
- Member message in a session-bound thread enqueues runner.send_input.v1
- Non-member message is rejected with a reply
- Bot messages are silently ignored
- Webhook messages are silently ignored
- Non-thread channel messages are silently ignored
- Messages in threads not bound to any session are silently ignored
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
import pytest_asyncio

from discode.db.engine import make_engine, make_session_factory
from discode.db.models import Guild, RunnerHost, User
from discode.db.models import Session as SessionModel

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_db_factory(migrated_db_url: str):
    engine = make_engine(migrated_db_url)
    factory = make_session_factory(engine)
    return engine, factory


@pytest_asyncio.fixture
async def db_factory_fixture(migrated_db_url):
    engine = make_engine(migrated_db_url)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


def _unique():
    h = uuid.uuid4().hex[:8]
    return f"guild-{h}", f"user-{h}", f"host-{h}"


async def _seed_session(db_factory, *, thread_id: str, owner_id: str | None = None) -> dict:
    """Seed a running session with the given thread_id. Returns a dict of ids."""
    guild_id, default_user_id, host_id = _unique()
    user_id = owner_id or default_user_id
    session_id = str(uuid.uuid4())
    session_uuid = uuid.UUID(session_id)

    async with db_factory() as db:
        db.add(Guild(id=guild_id, name="Test Guild"))
        db.add(User(id=user_id, username="testuser"))
        db.add(RunnerHost(id=host_id, status="online"))
        db.add(
            SessionModel(
                id=session_uuid,
                guild_id=guild_id,
                owner_id=user_id,
                tool="claude",
                name="test-session",
                cwd="/home/user",
                cwd_realpath="/home/user",
                parent_channel_id="channel-001",
                thread_id=thread_id,
                host_id=host_id,
                hook_secret_hash="hash",
                status="running",
            )
        )
        await db.commit()

    return {
        "session_id": session_id,
        "guild_id": guild_id,
        "user_id": user_id,
        "host_id": host_id,
        "thread_id": thread_id,
    }


def _make_message(
    *,
    content: str = "hello world",
    author_id: str = "user-123",
    author_is_bot: bool = False,
    webhook_id: int | None = None,
    channel_type: str = "thread",  # "thread" or "text"
    channel_id: int = 999_001,
    guild_id: int = 888_001,
) -> MagicMock:
    """Build a minimal discord.Message mock."""
    msg = MagicMock(spec=discord.Message)
    msg.content = content
    msg.webhook_id = webhook_id

    # Author
    author = MagicMock()
    numeric = author_id.replace("user-", "") if author_id.startswith("user-") else author_id
    author.id = int(numeric)
    author.bot = author_is_bot
    msg.author = author

    # Channel
    if channel_type == "thread":
        channel = MagicMock(spec=discord.Thread)
        channel.id = channel_id
    else:
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
    msg.channel = channel

    # Guild
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    msg.guild = guild

    msg.reply = AsyncMock()
    msg.add_reaction = AsyncMock()
    return msg


# ---------------------------------------------------------------------------
# resolve_session_for_thread
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_session_for_thread_returns_row(db_factory_fixture):
    """resolve_session_for_thread returns the session row when thread_id matches."""
    from discode.bot.session_service import resolve_session_for_thread

    tid = f"t-{uuid.uuid4().hex[:10]}"
    ids = await _seed_session(db_factory_fixture, thread_id=tid)

    async with db_factory_fixture() as db:
        row = await resolve_session_for_thread(db, thread_id=tid, guild_id=ids["guild_id"])

    assert row is not None
    assert str(row.id) == ids["session_id"]
    assert row.thread_id == tid


@pytest.mark.asyncio
async def test_resolve_session_for_thread_wrong_guild_returns_none(db_factory_fixture):
    """resolve_session_for_thread returns None when guild_id doesn't match."""
    from discode.bot.session_service import resolve_session_for_thread

    tid = f"t-{uuid.uuid4().hex[:10]}"
    await _seed_session(db_factory_fixture, thread_id=tid)

    async with db_factory_fixture() as db:
        row = await resolve_session_for_thread(db, thread_id=tid, guild_id="guild-wrong")

    assert row is None


@pytest.mark.asyncio
async def test_resolve_session_for_thread_missing_returns_none(db_factory_fixture):
    """resolve_session_for_thread returns None for unknown thread_id."""
    from discode.bot.session_service import resolve_session_for_thread

    async with db_factory_fixture() as db:
        row = await resolve_session_for_thread(db, thread_id="t-nonexistent", guild_id="guild-none")

    assert row is None


# ---------------------------------------------------------------------------
# is_session_member
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_session_member_owner_returns_true(db_factory_fixture):
    """Owner is always a member."""
    from discode.bot.session_service import is_session_member

    tid = f"t-{uuid.uuid4().hex[:10]}"
    ids = await _seed_session(db_factory_fixture, thread_id=tid)

    async with db_factory_fixture() as db:
        result = await is_session_member(db, session_id=ids["session_id"], user_id=ids["user_id"])

    assert result is True


@pytest.mark.asyncio
async def test_is_session_member_stranger_returns_false(db_factory_fixture):
    """A user not in session_members (and not the owner) returns False."""
    from discode.bot.session_service import is_session_member

    tid = f"t-{uuid.uuid4().hex[:10]}"
    ids = await _seed_session(db_factory_fixture, thread_id=tid)

    async with db_factory_fixture() as db:
        result = await is_session_member(db, session_id=ids["session_id"], user_id="user-stranger")

    assert result is False


# ---------------------------------------------------------------------------
# on_message handler — pure unit tests (no DB, mock everything)
#
# Since on_message uses deferred imports (imports inside the function body,
# matching the existing pattern in main.py), we patch the source modules
# rather than attributes on discode.bot.main.
# ---------------------------------------------------------------------------


def _make_engine_factory_mocks():
    """Return (mock_engine, mock_factory, mock_db_ctx) wired together."""
    mock_engine = MagicMock()
    mock_engine.dispose = AsyncMock()

    mock_db_ctx = AsyncMock()
    mock_db_obj = AsyncMock()
    mock_db_ctx.__aenter__ = AsyncMock(return_value=mock_db_obj)
    mock_db_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock(return_value=mock_db_ctx)
    return mock_engine, mock_factory, mock_db_ctx


@pytest.mark.asyncio
async def test_bot_message_ignored():
    """Messages authored by bots are silently dropped before any DB call."""
    from discode.bot.main import DiscodeBot

    bot = object.__new__(DiscodeBot)
    msg = _make_message(author_is_bot=True)

    with patch("discode.db.engine.make_engine") as mock_engine:
        await DiscodeBot.on_message(bot, msg)
        mock_engine.assert_not_called()

    msg.reply.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_message_ignored():
    """Messages with a webhook_id are silently dropped before any DB call."""
    from discode.bot.main import DiscodeBot

    bot = object.__new__(DiscodeBot)
    msg = _make_message(webhook_id=123456)

    with patch("discode.db.engine.make_engine") as mock_engine:
        await DiscodeBot.on_message(bot, msg)
        mock_engine.assert_not_called()

    msg.reply.assert_not_called()


@pytest.mark.asyncio
async def test_non_thread_message_ignored():
    """Messages in non-thread channels are silently dropped before any DB call."""
    from discode.bot.main import DiscodeBot

    bot = object.__new__(DiscodeBot)
    msg = _make_message(channel_type="text")

    with patch("discode.db.engine.make_engine") as mock_engine:
        await DiscodeBot.on_message(bot, msg)
        mock_engine.assert_not_called()

    msg.reply.assert_not_called()


def _mock_settings(db_url: str = "postgresql+asyncpg://test/test"):
    """Return a mock settings object with database_url set."""
    s = MagicMock()
    s.database_url = db_url
    return s


@pytest.mark.asyncio
async def test_no_session_for_thread_ignored():
    """Messages in threads not bound to any session are silently dropped."""
    import discode.config as _cfg
    from discode.bot.main import DiscodeBot

    bot = object.__new__(DiscodeBot)
    msg = _make_message(channel_type="thread", channel_id=777_001, guild_id=888_001)

    mock_engine, mock_factory, _ = _make_engine_factory_mocks()

    with (
        patch.object(_cfg, "settings", _mock_settings(), create=True),
        patch("discode.db.engine.make_engine", return_value=mock_engine),
        patch("discode.db.engine.make_session_factory", return_value=mock_factory),
        patch(
            "discode.bot.session_service.resolve_session_for_thread",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        await DiscodeBot.on_message(bot, msg)

    msg.reply.assert_not_called()


@pytest.mark.asyncio
async def test_non_member_message_rejected():
    """Non-member message receives a rejection reply."""
    import discode.config as _cfg
    from discode.bot.main import DiscodeBot

    bot = object.__new__(DiscodeBot)
    msg = _make_message(channel_type="thread", channel_id=777_002, guild_id=888_002)

    mock_row = MagicMock()
    mock_row.id = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
    mock_row.host_id = "host-abc"
    mock_row.guild_id = "guild-888002"
    mock_row.thread_id = "777002"
    mock_row.owner_id = "user-owner"

    mock_engine, mock_factory, _ = _make_engine_factory_mocks()

    with (
        patch.object(_cfg, "settings", _mock_settings(), create=True),
        patch("discode.db.engine.make_engine", return_value=mock_engine),
        patch("discode.db.engine.make_session_factory", return_value=mock_factory),
        patch(
            "discode.bot.session_service.resolve_session_for_thread",
            new_callable=AsyncMock,
            return_value=mock_row,
        ),
        patch(
            "discode.bot.session_service.is_session_member",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        await DiscodeBot.on_message(bot, msg)

    msg.reply.assert_called_once()
    call_args = msg.reply.call_args[0][0]
    assert "not a member" in call_args.lower() or "invite" in call_args.lower()


@pytest.mark.asyncio
async def test_member_message_in_session_thread_enqueues_input():
    """Authorized member message calls run_input_saga with the message content."""
    import discode.config as _cfg
    from discode.bot.main import DiscodeBot

    bot = object.__new__(DiscodeBot)
    msg = _make_message(
        content="run the tests",
        channel_type="thread",
        channel_id=777_003,
        guild_id=888_003,
    )

    mock_row = MagicMock()
    mock_row.id = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
    mock_row.host_id = "host-xyz"
    mock_row.guild_id = "guild-888003"
    mock_row.thread_id = "777003"
    mock_row.owner_id = "user-owner"

    mock_engine, mock_factory, _ = _make_engine_factory_mocks()

    with (
        patch.object(_cfg, "settings", _mock_settings(), create=True),
        patch("discode.db.engine.make_engine", return_value=mock_engine),
        patch("discode.db.engine.make_session_factory", return_value=mock_factory),
        patch(
            "discode.bot.session_service.resolve_session_for_thread",
            new_callable=AsyncMock,
            return_value=mock_row,
        ),
        patch(
            "discode.bot.session_service.is_session_member",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "discode.bot.sagas.input.run_input_saga",
            new_callable=AsyncMock,
            return_value="ikey-abc",
        ) as mock_saga,
    ):
        await DiscodeBot.on_message(bot, msg)

    mock_saga.assert_called_once()
    call_kwargs = mock_saga.call_args.kwargs
    assert call_kwargs["text_content"] == "run the tests"
    assert call_kwargs["session_id"] == str(mock_row.id)
    msg.reply.assert_not_called()  # no error reply on success
