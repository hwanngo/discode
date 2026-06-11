from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (sa.PrimaryKeyConstraint("envelope_type", "idempotency_key"),)

    envelope_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.UUID(), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    outcome: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    claim_owner: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
