from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AllowedRoot(Base):
    __tablename__ = "allowed_roots"
    __table_args__ = (
        sa.CheckConstraint("realpath LIKE '/%'", name="allowed_roots_realpath_check"),
        sa.UniqueConstraint(
            "guild_id", "user_id", "runner_host_id", "realpath", name="allowed_roots_uq"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    guild_id: Mapped[str | None] = mapped_column(
        sa.Text, sa.ForeignKey("guilds.id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="")
    runner_host_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    realpath: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
