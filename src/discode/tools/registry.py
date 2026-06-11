from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from discode.db.models import ToolDefinition

from .base import ToolAdapter
from .errors import UnknownTool
from .generic import GenericAdapter


async def get_adapter(session: AsyncSession, name: str) -> tuple[ToolAdapter, ToolDefinition]:
    row = await session.scalar(
        select(ToolDefinition).where(
            ToolDefinition.name == name,
            ToolDefinition.enabled.is_(True),
        )
    )
    if row is None:
        raise UnknownTool(name)
    return GenericAdapter(row), row
