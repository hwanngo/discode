# Envelope type registry and pydantic schemas.
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

ENVELOPE_TYPES: frozenset[str] = frozenset(
    {
        "runner.create_session.v1",
        "runner.send_input.v1",
        "runner.resume_session.v1",
        "runner.stop_session.v1",
        "discord.output_chunk.v1",
        "discord.system_notice.v1",
        "discord.terminal_notice.v1",
        "discord.archive_thread.v1",
    }
)


class CreateSessionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str  # "runner.create_session.v1"
    idempotency_key: str
    session_id: str
    guild_id: str
    owner_id: str
    tool: str
    cwd_realpath: str
    thread_id: str
    hook_secret_ciphertext: str
    model: str | None = None
    resume_token: str | None = None


class SendInputEnvelope(BaseModel):
    type: str  # "runner.send_input.v1"
    idempotency_key: str
    session_id: str
    guild_id: str
    thread_id: str
    host_id: str
    text: str


class ResumeSessionEnvelope(BaseModel):
    type: str
    idempotency_key: str
    session_id: str
    guild_id: str
    thread_id: str


class StopSessionEnvelope(BaseModel):
    type: str
    idempotency_key: str
    session_id: str
    guild_id: str
    thread_id: str


class OutputChunkEnvelope(BaseModel):
    type: str
    idempotency_key: str
    session_id: str
    guild_id: str
    thread_id: str
    seq: int
    chunk_id: int


class SystemNoticeEnvelope(BaseModel):
    type: str
    idempotency_key: str
    session_id: str
    guild_id: str
    thread_id: str
    notice_type: str
    order_after_seq: int
    message: str


class TerminalNoticeEnvelope(BaseModel):
    type: str
    idempotency_key: str
    session_id: str
    guild_id: str
    thread_id: str
    message: str
    then_archive: bool = False
    alert_kind: str | None = None


class ArchiveThreadEnvelope(BaseModel):
    type: str
    idempotency_key: str
    session_id: str
    guild_id: str
    thread_id: str
