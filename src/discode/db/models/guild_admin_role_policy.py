from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class GuildAdminRolePolicy(Base):
    __tablename__ = "guild_admin_role_policy"
    __table_args__ = (sa.UniqueConstraint("guild_id", name="guild_admin_role_policy_guild_id_uq"),)

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    role_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    role_name_fallback: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    updated_by: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
