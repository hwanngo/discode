from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class SessionConfig(Base):
    __tablename__ = "session_config"
    __table_args__ = (
        sa.CheckConstraint(
            "(value IS NOT NULL) <> (value_ciphertext IS NOT NULL)",
            name="session_config_one_value",
        ),
        sa.CheckConstraint(
            "key ~ '^[a-z][a-z0-9_]{0,31}$'",
            name="session_config_key_format",
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(), ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
