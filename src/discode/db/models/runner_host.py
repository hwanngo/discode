from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class RunnerHost(Base):
    __tablename__ = "runner_hosts"
    __table_args__ = (
        sa.CheckConstraint("id ~ '^[a-z0-9-]{1,40}$'", name="runner_hosts_id_format_check"),
        sa.CheckConstraint(
            "status IN ('online','offline','draining','unknown')",
            name="runner_hosts_status_check",
        ),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="unknown")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    # Named host_metadata to avoid shadowing DeclarativeBase.metadata
    host_metadata: Mapped[dict[str, object] | None] = mapped_column(
        "metadata", sa.JSON, nullable=False, server_default=sa.text("'{}'")
    )
