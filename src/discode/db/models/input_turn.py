from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class InputTurn(Base):
    """Durable execution/result ledger; uncertain executions are never auto-replayed."""

    __tablename__ = "input_turns"
    __table_args__ = (
        sa.Index(
            "input_turns_delivered_retention_idx",
            "updated_at",
            postgresql_where=sa.text(
                "status IN ('delivered','delivery_failed') "
                "AND (result_text IS NOT NULL OR resume_token IS NOT NULL)"
            ),
        ),
        sa.CheckConstraint(
            "status IN ('queued','executing','completed','delivered','uncertain',"
            "'delivery_failed','aborted')",
            name="input_turns_status_check",
        ),
        sa.Index("input_turns_session_created_idx", "session_id", "created_at"),
    )
    sequence: Mapped[int] = mapped_column(
        sa.BigInteger, sa.Identity(always=True), unique=True, nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    lifecycle_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    result_text: Mapped[str | None] = mapped_column(sa.Text)
    resume_token: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
