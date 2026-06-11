from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class PathAlias(Base):
    __tablename__ = "path_aliases"
    __table_args__ = (
        sa.UniqueConstraint("guild_id", "alias", name="path_aliases_uq"),
        sa.CheckConstraint(
            "alias ~ '^[a-z][a-z0-9_-]{0,63}$'",
            name="path_aliases_alias_check",
        ),
        sa.CheckConstraint(
            "realpath LIKE '/%'",
            name="path_aliases_realpath_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    guild_id: Mapped[str] = mapped_column(
        sa.Text, sa.ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(sa.Text, nullable=False)
    path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    realpath: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
