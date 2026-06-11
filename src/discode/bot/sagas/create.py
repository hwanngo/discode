from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discode.db.models import (
    Event,
    Guild,
    SessionMember,
    User,
)
from discode.db.models import (
    Session as SessionModel,
)
from discode.queues.outbox import write_outbox
from discode.security.fernet import generate_hook_secret, load_multifernet

logger = logging.getLogger(__name__)


@dataclass
class CreateSessionResult:
    session_id: str
    hook_secret_ciphertext: str  # encrypted to deployment key
    resumed_from_session_id: str | None = field(default=None)


async def run_create_saga(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    guild_id: str,
    owner_id: str,
    owner_username: str,
    tool: str,  # "claude"|"opencode"|"codex"
    name: str,
    cwd: str,
    cwd_realpath: str,
    parent_channel_id: str,
    runner_host_id: str,
    deployment_secret_key: str,
    thread_id: str = "",
    model: str | None = None,
    resume_token: str | None = None,
) -> CreateSessionResult:
    """Create saga (bot side):

    1. Ensure Guild and User rows exist.
    2. Resume-by-name: pick up the latest archived session with the same name.
    3. Generate hook secret, encrypt to deployment key.
    4. In ONE tx: insert Session('creating') + SessionMember(owner) +
       counters + outbox(runner.create_session.v1).
    5. Return CreateSessionResult.
    """
    session_id = str(uuid.uuid4())
    idempotency_key = str(uuid.uuid4())

    # Generate hook secret and encrypt it
    secret = generate_hook_secret()
    hook_secret_hash = hashlib.sha256(secret).hexdigest()
    fernet = load_multifernet(deployment_secret_key)
    ciphertext = fernet.encrypt(secret).decode()

    # Resume-by-name: check for archived session with same (name, guild_id, owner_id)
    resume_token_to_use = resume_token
    archived_session_id: str | None = None

    async with db_factory() as db:
        result = await db.execute(
            text("""
                SELECT id, tool_resume_token
                FROM sessions
                WHERE name = :name AND guild_id = :gid AND owner_id = :uid
                  AND status = 'archived'
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"name": name, "gid": guild_id, "uid": owner_id},
        )
        archived_row = result.fetchone()

    if archived_row is not None:
        archived_session_id = str(archived_row[0])
        existing_resume_token = archived_row[1]

        if resume_token_to_use is None:
            resume_token_to_use = existing_resume_token

        # Mark the archived row as superseded
        async with db_factory() as db:
            async with db.begin():
                await db.execute(
                    text("UPDATE sessions SET status_reason='superseded' WHERE id=:sid"),
                    {"sid": archived_session_id},
                )

    async with db_factory() as db:
        # Step 1: Upsert Guild
        await db.execute(
            pg_insert(Guild)
            .values(id=guild_id, name=guild_id)
            .on_conflict_do_nothing(index_elements=["id"])
        )

        # Step 1: Upsert User
        await db.execute(
            pg_insert(User)
            .values(id=owner_id, username=owner_username)
            .on_conflict_do_nothing(index_elements=["id"])
        )

        # Multiple sessions can coexist in the same cwd: each user message
        # spawns a fresh, short-lived subprocess, so there's nothing for two
        # sessions to collide on (no port file, no shared serve process).

        # Step 4: Insert all rows in one transaction
        session_uuid = uuid.UUID(session_id)

        db.add(
            SessionModel(
                id=session_uuid,
                guild_id=guild_id,
                owner_id=owner_id,
                tool=tool,
                name=name,
                cwd=cwd,
                cwd_realpath=cwd_realpath,
                parent_channel_id=parent_channel_id,
                thread_id=thread_id or None,
                host_id=runner_host_id,
                hook_secret_hash=hook_secret_hash,
                status="creating",
            )
        )
        await db.flush()

        db.add(SessionMember(session_id=session_uuid, user_id=owner_id, role="owner"))
        db.add(
            Event(
                idempotency_key=str(uuid.uuid4()),
                guild_id=guild_id,
                session_id=session_uuid,
                actor_id=owner_id,
                type="session.create.requested",
                payload={"tool": tool, "cwd": cwd_realpath},
            )
        )

        await write_outbox(
            db,
            producer="bot",
            stream_key=f"runner:jobs:{runner_host_id}",
            envelope_type="runner.create_session.v1",
            payload={
                "type": "runner.create_session.v1",
                "idempotency_key": idempotency_key,
                "session_id": session_id,
                "guild_id": guild_id,
                "owner_id": owner_id,
                "tool": tool,
                "cwd_realpath": cwd_realpath,
                "thread_id": thread_id,
                "hook_secret_ciphertext": ciphertext,
                "model": model,
                "resume_token": resume_token_to_use,
            },
        )

        await db.commit()

    # Codex resumes via its own --resume <session_id> mechanism (the
    # tool_resume_token carries forward through resume-by-name above).
    # No tool_messages clone-forward needed — codex handles its own history.

    return CreateSessionResult(
        session_id=session_id,
        hook_secret_ciphertext=ciphertext,
        resumed_from_session_id=archived_session_id,
    )
