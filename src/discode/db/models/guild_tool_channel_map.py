from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class GuildToolChannelMap(Base):
    __tablename__ = "guild_tool_channel_map"
    __table_args__ = (
        sa.UniqueConstraint("guild_id", "tool", name="guild_tool_channel_map_guild_tool_uq"),
    )

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    tool: Mapped[str] = mapped_column(sa.Text, nullable=False)
    channel_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    updated_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
