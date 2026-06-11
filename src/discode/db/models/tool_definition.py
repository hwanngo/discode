from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from sqlalchemy import JSON, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ToolDefinition(Base):
    __tablename__ = "tool_definitions"
    __table_args__ = (
        sa.CheckConstraint(
            "name ~ '^[a-z][a-z0-9_-]{0,63}$'",
            name="tool_definitions_name_format",
        ),
    )

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    argv_prefix: Mapped[list] = mapped_column(JSON, nullable=False)
    argv_resume_tokens: Mapped[list] = mapped_column(JSON, nullable=False)
    argv_suffix: Mapped[list] = mapped_column(JSON, nullable=False)
    uses_pty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resume_token_source: Mapped[str] = mapped_column(String(16), nullable=False)
    resume_token_pattern: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reply_extractor: Mapped[str] = mapped_column(String(32), nullable=False)
    reply_json_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extra_paths: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    env_passthrough: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    model_flag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_key_env: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_url_env: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
