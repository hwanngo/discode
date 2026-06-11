from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('creating','running','idle','archived','resuming',"
            "'stopping','stopped','failed','orphaned')",
            name="sessions_status_check",
        ),
        sa.CheckConstraint(
            "(thread_archive_dead_lettered_at IS NULL) "
            "OR (thread_archive_requested_at IS NOT NULL)",
            name="sessions_archive_deadletter_check",
        ),
        sa.Index("sessions_guild_status_idx", "guild_id", "status"),
        sa.Index("sessions_host_status_idx", "host_id", "status"),
        sa.Index("sessions_owner_idx", "owner_id"),
        sa.Index(
            "sessions_archive_pending_idx",
            "id",
            postgresql_where=sa.text(
                "thread_archive_requested_at IS NOT NULL AND discord_thread_archived_at IS NULL"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    guild_id: Mapped[str | None] = mapped_column(
        sa.Text, sa.ForeignKey("guilds.id", ondelete="CASCADE"), nullable=True
    )
    owner_id: Mapped[str | None] = mapped_column(sa.Text, sa.ForeignKey("users.id"), nullable=True)
    tool: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    cwd: Mapped[str] = mapped_column(sa.Text, nullable=False)
    cwd_realpath: Mapped[str] = mapped_column(sa.Text, nullable=False)
    parent_channel_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    thread_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True, unique=True)
    host_id: Mapped[str | None] = mapped_column(
        sa.Text, sa.ForeignKey("runner_hosts.id"), nullable=True
    )
    hook_secret_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    hook_secret_ciphertext: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    shim_token_hash: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    tool_resume_token: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    running_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    recovery_checked_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    stopped_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    discord_thread_archived_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    thread_archive_requested_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    thread_archive_dead_lettered_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    transcript_bytes_used: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default="0"
    )
    repair_attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")


class SessionMember(Base):
    __tablename__ = "session_members"
    __table_args__ = (
        sa.CheckConstraint(
            "role IN ('owner','operator','viewer')",
            name="session_members_role_check",
        ),
        sa.PrimaryKeyConstraint("session_id", "user_id"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(), sa.ForeignKey("sessions.id", ondelete="CASCADE")
    )
    user_id: Mapped[str] = mapped_column(sa.Text, sa.ForeignKey("users.id"))
    role: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
