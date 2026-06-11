from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class RedisOutbox(Base):
    __tablename__ = "redis_outbox"
    __table_args__ = (
        sa.Index(
            "redis_outbox_pending_idx",
            "producer",
            "published_at",
            "next_attempt_at",
            postgresql_where=sa.text("published_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    producer: Mapped[str] = mapped_column(sa.Text, nullable=False)
    envelope_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    stream_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(sa.JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
