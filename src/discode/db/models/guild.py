from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Guild(Base):
    __tablename__ = "guilds"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('active','inactive','draining','blocked')",
            name="guilds_status_check",
        ),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="active")
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.text("now()")
    )


class GuildSettings(Base):
    __tablename__ = "guild_settings"

    guild_id: Mapped[str] = mapped_column(sa.Text, sa.ForeignKey("guilds.id"), primary_key=True)
    settings: Mapped[dict[str, object] | None] = mapped_column(
        sa.JSON, nullable=False, server_default=sa.text("'{}'")
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    username: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.text("now()")
    )
