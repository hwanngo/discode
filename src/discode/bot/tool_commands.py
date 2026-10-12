"""Tool administration and tool autocomplete Discord interface."""

from __future__ import annotations

import json

import discord
from discord import app_commands

from discode.bot.interaction_response import send_deferred_text


class ToolCommands(
    app_commands.Group,
    name="tool",
    description="Manage registered tool definitions",
):
    @app_commands.command(name="register", description="Register a new tool definition (admin)")
    @app_commands.describe(
        name="Tool identifier (lowercase, e.g. aider)",
        config="JSON config object",
    )
    async def tool_register(self, interaction: discord.Interaction, name: str, config: str) -> None:
        from discode.bot.tool_service import register_tool
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild is None or interaction.user is None:
            await interaction.response.send_message(
                "This command must be used inside a server.", ephemeral=True
            )
            return

        try:
            cfg = json.loads(config)
        except json.JSONDecodeError as e:
            await interaction.response.send_message(f"Invalid JSON: {e}", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        member = interaction.user
        member_roles = [str(r.id) for r in getattr(member, "roles", [])]
        member_role_names = [r.name for r in getattr(member, "roles", [])]

        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                result = await register_tool(
                    session,
                    guild_id=guild_id,
                    name=name,
                    config=cfg,
                    created_by=str(member.id),
                    member_roles=member_roles,
                    member_role_names=member_role_names,
                    user_id=str(interaction.user.id),
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.followup.send(result.message, ephemeral=True)

    @app_commands.command(name="list", description="List all registered tools")
    async def tool_list(self, interaction: discord.Interaction) -> None:
        from discode.bot.tool_service import list_tools
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command must be used inside a server.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                result = await list_tools(session)
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        if not result.tools:
            await interaction.followup.send("No tools registered.", ephemeral=True)
            return

        lines = [
            f"{t.name} • {t.display_name} • {'enabled' if t.enabled else 'disabled'}"
            for t in result.tools[:25]
        ]
        await send_deferred_text(interaction, "\n".join(lines))

    @app_commands.command(name="show", description="Show details for a tool")
    @app_commands.describe(name="Tool identifier")
    async def tool_show(self, interaction: discord.Interaction, name: str) -> None:
        from discode.bot.tool_service import show_tool
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command must be used inside a server.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                result = await show_tool(session, name=name)
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        if result.tool is None:
            await interaction.followup.send(result.message, ephemeral=True)
            return

        t = result.tool
        fields = [
            f"name:                {t.name}",
            f"display_name:        {t.display_name}",
            f"enabled:             {t.enabled}",
            f"argv_prefix:         {t.argv_prefix}",
            f"argv_resume_tokens:  {t.argv_resume_tokens}",
            f"argv_suffix:         {t.argv_suffix}",
            f"uses_pty:            {t.uses_pty}",
            f"resume_token_source: {t.resume_token_source}",
            f"resume_token_pattern:{t.resume_token_pattern}",
            f"reply_extractor:     {t.reply_extractor}",
            f"reply_json_field:    {t.reply_json_field}",
            f"extra_paths:         {t.extra_paths}",
            f"env_passthrough:     {t.env_passthrough}",
        ]
        await send_deferred_text(interaction, "\n".join(fields))

    @app_commands.command(name="update", description="Update a single field on a tool (admin)")
    @app_commands.describe(name="Tool identifier", field="Field name", value="New value")
    async def tool_update(
        self, interaction: discord.Interaction, name: str, field: str, value: str
    ) -> None:
        from discode.bot.tool_service import update_tool_field
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild is None or interaction.user is None:
            await interaction.response.send_message(
                "This command must be used inside a server.", ephemeral=True
            )
            return

        guild_id = str(interaction.guild.id)
        member = interaction.user
        member_roles = [str(r.id) for r in getattr(member, "roles", [])]
        member_role_names = [r.name for r in getattr(member, "roles", [])]

        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                result = await update_tool_field(
                    session,
                    guild_id=guild_id,
                    name=name,
                    field=field,
                    value=value,
                    member_roles=member_roles,
                    member_role_names=member_role_names,
                    user_id=str(interaction.user.id),
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.followup.send(result.message, ephemeral=True)

    @app_commands.command(name="enable", description="Enable a tool (admin)")
    @app_commands.describe(name="Tool identifier")
    async def tool_enable(self, interaction: discord.Interaction, name: str) -> None:
        from discode.bot.tool_service import enable_tool
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild is None or interaction.user is None:
            await interaction.response.send_message(
                "This command must be used inside a server.", ephemeral=True
            )
            return

        guild_id = str(interaction.guild.id)
        member = interaction.user
        member_roles = [str(r.id) for r in getattr(member, "roles", [])]
        member_role_names = [r.name for r in getattr(member, "roles", [])]

        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                result = await enable_tool(
                    session,
                    guild_id=guild_id,
                    name=name,
                    member_roles=member_roles,
                    member_role_names=member_role_names,
                    user_id=str(interaction.user.id),
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.followup.send(result.message, ephemeral=True)

    @app_commands.command(name="disable", description="Disable a tool (admin)")
    @app_commands.describe(name="Tool identifier")
    async def tool_disable(self, interaction: discord.Interaction, name: str) -> None:
        from discode.bot.tool_service import disable_tool
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild is None or interaction.user is None:
            await interaction.response.send_message(
                "This command must be used inside a server.", ephemeral=True
            )
            return

        guild_id = str(interaction.guild.id)
        member = interaction.user
        member_roles = [str(r.id) for r in getattr(member, "roles", [])]
        member_role_names = [r.name for r in getattr(member, "roles", [])]

        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                result = await disable_tool(
                    session,
                    guild_id=guild_id,
                    name=name,
                    member_roles=member_roles,
                    member_role_names=member_role_names,
                    user_id=str(interaction.user.id),
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.followup.send(result.message, ephemeral=True)

    @app_commands.command(name="remove", description="Remove a tool definition (admin)")
    @app_commands.describe(name="Tool identifier")
    async def tool_remove(self, interaction: discord.Interaction, name: str) -> None:
        from discode.bot.tool_service import remove_tool
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild is None or interaction.user is None:
            await interaction.response.send_message(
                "This command must be used inside a server.", ephemeral=True
            )
            return

        guild_id = str(interaction.guild.id)
        member = interaction.user
        member_roles = [str(r.id) for r in getattr(member, "roles", [])]
        member_role_names = [r.name for r in getattr(member, "roles", [])]

        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                result = await remove_tool(
                    session,
                    guild_id=guild_id,
                    name=name,
                    member_roles=member_roles,
                    member_role_names=member_role_names,
                    user_id=str(interaction.user.id),
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.followup.send(result.message, ephemeral=True)


# ---------------------------------------------------------------------------
# /start autocomplete helper
# ---------------------------------------------------------------------------


async def _tool_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    from discode.bot.tool_service import list_tools
    from discode.config import settings
    from discode.db.engine import get_shared_engine, make_session_factory

    engine = get_shared_engine(settings.database_url)
    try:
        factory = make_session_factory(engine)
        async with factory() as session:
            result = await list_tools(session)
    finally:
        pass  # shared engine: pooled for bot lifetime, not disposed per-request

    return [
        app_commands.Choice(name=f"{t.display_name} ({t.name})", value=t.name)
        for t in result.tools
        if t.enabled and current.lower() in t.name.lower()
    ][:25]


async def _start_tool_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await _tool_autocomplete(interaction, current)
