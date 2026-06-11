from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def emit_event(
    session: AsyncSession,
    *,
    type: str,
    guild_id: str | None = None,
    session_id: str | None = None,
    actor_id: str | None = None,
    payload: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> Event:
    """Insert an audit event in the caller's transaction."""
    from discode.specs.audit_events import AUDIT_EVENT_TYPES

    if type not in AUDIT_EVENT_TYPES:
        raise ValueError(f"unknown audit event type: {type}")

    import uuid as uuid_mod

    event = Event(
        idempotency_key=str(uuid_mod.uuid4()),
        correlation_id=correlation_id,
        guild_id=guild_id,
        session_id=uuid_mod.UUID(session_id) if session_id else None,
        actor_id=actor_id,
        type=type,
        payload=payload or {},
    )
    session.add(event)
    return event


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str | None] = mapped_column(sa.Text, nullable=True, unique=True)
    correlation_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    guild_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.UUID(), sa.ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    actor_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    tool: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    cwd_realpath: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        sa.JSON, nullable=False, server_default=sa.text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
