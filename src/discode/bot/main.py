from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from discode.queues.relay import RelayLoop

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /setup map subgroup
# ---------------------------------------------------------------------------


class SetupMapGroup(app_commands.Group, name="map", description="Manage tool→channel mappings"):
    @app_commands.command(name="set", description="Map a tool to a channel")
    @app_commands.describe(tool="The tool name (e.g. claude, codex)", channel="The target channel")
    async def map_set(
        self,
        interaction: discord.Interaction,
        tool: str,
        channel: discord.TextChannel,
    ) -> None:
        from discode.bot.setup_service import setup_map_set
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

        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                result = await setup_map_set(
                    session,
                    guild_id=guild_id,
                    tool=tool,
                    channel_id=str(channel.id),
                    updated_by=str(member.id),
                    member_roles=member_roles,
                    member_role_names=member_role_names,
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.response.send_message(result.message, ephemeral=True)

    @app_commands.command(name="list", description="List all tool→channel mappings for this guild")
    async def map_list(self, interaction: discord.Interaction) -> None:
        from discode.bot.setup_service import setup_map_list
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command must be used inside a server.", ephemeral=True
            )
            return

        guild_id = str(interaction.guild.id)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                result = await setup_map_list(session, guild_id=guild_id)
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        if not result.mappings:
            await interaction.response.send_message(
                "No tool→channel mappings configured.", ephemeral=True
            )
            return

        lines = [f"• `{m.tool}` → <#{m.channel_id}>" for m in result.mappings]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="remove", description="Remove a tool→channel mapping")
    @app_commands.describe(tool="The tool name to unmap")
    async def map_remove(self, interaction: discord.Interaction, tool: str) -> None:
        from discode.bot.setup_service import setup_map_remove
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

        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                result = await setup_map_remove(
                    session,
                    guild_id=guild_id,
                    tool=tool,
                    member_roles=member_roles,
                    member_role_names=member_role_names,
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.response.send_message(result.message, ephemeral=True)


# ---------------------------------------------------------------------------
# /setup admin-role subgroup
# ---------------------------------------------------------------------------


class SetupAdminRoleGroup(
    app_commands.Group, name="admin-role", description="Manage the Discode admin role policy"
):
    @app_commands.command(name="set", description="Set the admin role for Discode commands")
    @app_commands.describe(role="The Discord role to grant admin access")
    async def admin_role_set(self, interaction: discord.Interaction, role: discord.Role) -> None:
        from discode.bot.setup_service import setup_admin_role_set
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

        # Bootstrap-safe gate: setting/changing the admin role itself must require
        # the Discord 'Manage Server' permission OR the currently-configured admin
        # role. The open DB check alone would let any member self-promote in a
        # guild that hasn't set an admin role yet.
        has_manage_server = False
        if isinstance(member, discord.Member):
            has_manage_server = member.guild_permissions.manage_guild

        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                if not has_manage_server:
                    from discode.bot.policies.admin_auth import is_guild_admin_authorized

                    is_admin = await is_guild_admin_authorized(
                        session,
                        guild_id=guild_id,
                        member_roles=member_roles,
                        member_role_names=member_role_names,
                    )
                    if not is_admin:
                        await interaction.response.send_message(
                            "You need the 'Manage Server' Discord permission or the configured "
                            "admin role to set the admin role.",
                            ephemeral=True,
                        )
                        return
                result = await setup_admin_role_set(
                    session,
                    guild_id=guild_id,
                    role_id=str(role.id),
                    role_name=role.name,
                    updated_by=str(member.id),
                    member_roles=member_roles,
                    member_role_names=member_role_names,
                    already_authorized=True,
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.response.send_message(result.message, ephemeral=True)

    @app_commands.command(name="list", description="Show the current admin role policy")
    async def admin_role_list(self, interaction: discord.Interaction) -> None:
        from discode.bot.setup_service import setup_admin_role_list
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command must be used inside a server.", ephemeral=True
            )
            return

        guild_id = str(interaction.guild.id)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                result = await setup_admin_role_list(session, guild_id=guild_id)
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        if result.role_id is not None:
            msg = f"Admin role: <@&{result.role_id}>"
        elif result.role_name is not None:
            msg = f"Admin role (name fallback): `{result.role_name}`"
        else:
            msg = "No admin role configured (bootstrap mode — anyone can run setup commands)."

        await interaction.response.send_message(msg, ephemeral=True)


# ---------------------------------------------------------------------------
# /setup top-level group
# ---------------------------------------------------------------------------


class SetupGroup(app_commands.Group, name="setup", description="Discode guild setup commands"):
    def __init__(self) -> None:
        super().__init__()
        self.add_command(SetupMapGroup())
        self.add_command(SetupAdminRoleGroup())

    @app_commands.command(name="init", description="Initialize Discode for this guild")
    async def setup_init(self, interaction: discord.Interaction) -> None:
        """Bootstrap: anyone with Manage Server OR when no policy exists."""
        from discode.bot.setup_service import setup_init
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild is None or interaction.user is None:
            await interaction.response.send_message(
                "This command must be used inside a server.", ephemeral=True
            )
            return

        guild_id = str(interaction.guild.id)
        member = interaction.user

        # Bootstrap auth: Manage Server permission OR no policy exists yet
        has_manage_server = False
        if isinstance(member, discord.Member):
            has_manage_server = member.guild_permissions.manage_guild

        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                from discode.bot.policies.admin_auth import is_guild_admin_authorized

                is_admin = await is_guild_admin_authorized(
                    session,
                    guild_id=guild_id,
                    member_roles=[str(r.id) for r in getattr(member, "roles", [])],
                    member_role_names=[r.name for r in getattr(member, "roles", [])],
                )
                # Allow if: has Discord manage_guild perm OR bootstrap mode (no policy)
                if not has_manage_server and not is_admin:
                    await interaction.response.send_message(
                        "You need the 'Manage Server' Discord permission or the configured "
                        "admin role to run /setup init.",
                        ephemeral=True,
                    )
                    return

                result = await setup_init(session, guild_id=guild_id, invoked_by=str(member.id))
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.response.send_message(result.message, ephemeral=True)

    @app_commands.command(name="sync", description="Sync slash command registrations")
    async def setup_sync(self, interaction: discord.Interaction) -> None:
        from discode.bot.setup_service import setup_sync
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

        # Collect all guild IDs the bot is currently in so we sync them all.
        bot = interaction.client
        all_guild_ids = [g.id for g in bot.guilds] if bot.guilds else []

        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                report = await setup_sync(
                    session,
                    guild_id=guild_id,
                    invoked_by=str(member.id),
                    member_roles=member_roles,
                    member_role_names=member_role_names,
                    bot=bot,
                    extra_guild_ids=all_guild_ids,
                )
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        if not report.authorized:
            await interaction.response.send_message(report.message, ephemeral=True)
            return

        msg = (
            f"**Sync complete** (`{report.timestamp}`)\n"
            f"Global — added: {report.global_added}, "
            f"removed: {report.global_removed}\n"
            f"Guild — added: {report.guild_added}, "
            f"removed: {report.guild_removed}"
        )
        await interaction.response.send_message(msg, ephemeral=True)


# ---------------------------------------------------------------------------
# /root command group
# ---------------------------------------------------------------------------


class RootCommands(app_commands.Group, name="root", description="Manage allowed project roots"):
    @app_commands.command(
        name="add",
        description="Allow a project directory; optionally register a guild alias",
    )
    @app_commands.describe(
        path="Absolute path to allow",
        alias="Optional guild-shared shortname for this path (admin-only)",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        path: str,
        alias: str | None = None,
    ) -> None:
        from discode.bot.policies.admin_auth import is_guild_admin_authorized
        from discode.bot.setup_service import add_allowed_root_for_user, set_path_alias
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild_id is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        runner_host_id = (
            os.environ.get("RUNNER_HOST_ID")
            or os.environ.get("DISCODE_RUNNER_HOST_ID")
            or "local-dev"
        )
        member = interaction.user
        member_roles = [str(r.id) for r in getattr(member, "roles", [])]
        member_role_names = [r.name for r in getattr(member, "roles", [])]

        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            if alias is not None:
                async with factory() as session:
                    authorized = await is_guild_admin_authorized(
                        session,
                        guild_id=str(interaction.guild_id),
                        member_roles=member_roles,
                        member_role_names=member_role_names,
                    )
                if not authorized:
                    await interaction.followup.send(
                        "Only the guild's Discode admins can register aliases.",
                        ephemeral=True,
                    )
                    return
                # set_path_alias writes both the guild-wide allowed_roots row
                # AND the path_aliases row in one transaction.
                try:
                    alias_result = await set_path_alias(
                        factory,
                        guild_id=str(interaction.guild_id),
                        alias=alias,
                        path=path,
                        runner_host_id=runner_host_id,
                        created_by=str(interaction.user.id),
                    )
                except ValueError as exc:
                    await interaction.followup.send(f"Failed to register: {exc}", ephemeral=True)
                    return
                await interaction.followup.send(
                    f"Allowed root added: `{alias_result.realpath}` "
                    f"(alias `{alias_result.alias}` registered for the guild).",
                    ephemeral=True,
                )
                return

            try:
                root_result = await add_allowed_root_for_user(
                    factory,
                    guild_id=str(interaction.guild_id),
                    user_id=str(interaction.user.id),
                    runner_host_id=runner_host_id,
                    path=path,
                    created_by=str(interaction.user.id),
                )
            except ValueError as exc:
                await interaction.followup.send(f"Failed to add root: {exc}", ephemeral=True)
                return
            await interaction.followup.send(
                f"Allowed root added: `{root_result.realpath}`",
                ephemeral=True,
            )
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

    alias = app_commands.Group(
        name="alias",
        description="Manage guild-shared root path aliases",
    )

    @alias.command(
        name="set",
        description="Register or replace a guild alias for a project path (admin)",
    )
    async def alias_set(
        self,
        interaction: discord.Interaction,
        alias: str,
        path: str,
    ) -> None:
        from discode.bot.policies.admin_auth import is_guild_admin_authorized
        from discode.bot.setup_service import set_path_alias
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild_id is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        runner_host_id = (
            os.environ.get("RUNNER_HOST_ID")
            or os.environ.get("DISCODE_RUNNER_HOST_ID")
            or "local-dev"
        )
        member = interaction.user
        member_roles = [str(r.id) for r in getattr(member, "roles", [])]
        member_role_names = [r.name for r in getattr(member, "roles", [])]

        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                authorized = await is_guild_admin_authorized(
                    session,
                    guild_id=str(interaction.guild_id),
                    member_roles=member_roles,
                    member_role_names=member_role_names,
                )
            if not authorized:
                await interaction.followup.send(
                    "Only the guild's Discode admins can set aliases.",
                    ephemeral=True,
                )
                return
            try:
                result = await set_path_alias(
                    factory,
                    guild_id=str(interaction.guild_id),
                    alias=alias,
                    path=path,
                    runner_host_id=runner_host_id,
                    created_by=str(interaction.user.id),
                )
            except ValueError as exc:
                await interaction.followup.send(f"Could not set alias: {exc}", ephemeral=True)
                return
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.followup.send(
            f"Alias `{result.alias}` → `{result.realpath}` registered for this guild.",
            ephemeral=True,
        )

    @alias.command(name="list", description="List all aliases configured for this guild")
    async def alias_list(self, interaction: discord.Interaction) -> None:
        from discode.bot.setup_service import list_path_aliases
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild_id is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            rows = await list_path_aliases(factory, guild_id=str(interaction.guild_id))
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        if not rows:
            await interaction.followup.send(
                "No aliases configured. Ask an admin to run `/root alias set <name> <path>`.",
                ephemeral=True,
            )
            return
        body = "\n".join(f"`{r.alias}` → `{r.realpath}`" for r in rows)
        await interaction.followup.send(body, ephemeral=True)

    @alias.command(name="remove", description="Remove a guild alias (admin)")
    async def alias_remove(
        self,
        interaction: discord.Interaction,
        alias: str,
    ) -> None:
        from discode.bot.policies.admin_auth import is_guild_admin_authorized
        from discode.bot.setup_service import remove_path_alias
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild_id is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        member = interaction.user
        member_roles = [str(r.id) for r in getattr(member, "roles", [])]
        member_role_names = [r.name for r in getattr(member, "roles", [])]

        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                authorized = await is_guild_admin_authorized(
                    session,
                    guild_id=str(interaction.guild_id),
                    member_roles=member_roles,
                    member_role_names=member_role_names,
                )
            if not authorized:
                await interaction.followup.send(
                    "Only the guild's Discode admins can remove aliases.",
                    ephemeral=True,
                )
                return
            removed = await remove_path_alias(
                factory,
                guild_id=str(interaction.guild_id),
                alias=alias,
            )
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        if removed:
            await interaction.followup.send(f"Alias `{alias}` removed.", ephemeral=True)
        else:
            await interaction.followup.send(f"No such alias `{alias}`.", ephemeral=True)


# ---------------------------------------------------------------------------
# /session command group
# ---------------------------------------------------------------------------


class SessionCommands(app_commands.Group, name="session", description="Manage Discode sessions"):
    @app_commands.command(name="list", description="List your sessions")
    async def list(self, interaction: discord.Interaction) -> None:
        from discode.bot.commands.session_reads import get_sessions_for_user
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild_id is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            sessions = await get_sessions_for_user(
                factory,
                guild_id=str(interaction.guild_id),
                user_id=str(interaction.user.id),
            )
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        if not sessions:
            await interaction.followup.send("No sessions found.", ephemeral=True)
            return

        lines = [
            f"`{row['name']}` [{row['status']}] `{str(row['session_id'])[:8]}`"
            for row in sessions[:10]
        ]
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(name="archive", description="Archive a session")
    async def archive(self, interaction: discord.Interaction, session: str | None = None) -> None:
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild_id is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            from discode.bot.session_service import (
                archive_session,
                is_session_owner_or_admin,
                resolve_session_target,
            )

            target = await resolve_session_target(
                factory,
                guild_id=str(interaction.guild_id),
                user_id=str(interaction.user.id),
                session_ref=session,
            )
            if target is None:
                await interaction.followup.send(
                    "No matching session found. Provide a session id/name or "
                    "start a session first.",
                    ephemeral=True,
                )
                return
            if not await is_session_owner_or_admin(
                factory,
                session_id=str(target["session_id"]),
                guild_id=str(interaction.guild_id),
                user_id=str(interaction.user.id),
                member_roles=[str(r.id) for r in getattr(interaction.user, "roles", [])],
                member_role_names=[r.name for r in getattr(interaction.user, "roles", [])],
            ):
                await interaction.followup.send(
                    "Only the session owner or a guild admin can archive this session.",
                    ephemeral=True,
                )
                return
            archived = await archive_session(
                factory,
                session_id=str(target["session_id"]),
                guild_id=str(interaction.guild_id),
            )
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.followup.send(
            "Archive requested." if archived else "Session cannot be archived.",
            ephemeral=True,
        )

    @app_commands.command(name="resume", description="Resume an archived session")
    async def resume(self, interaction: discord.Interaction, session: str | None = None) -> None:
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild_id is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            from discode.bot.session_service import (
                is_session_owner_or_admin,
                resolve_session_target,
                resume_session,
            )

            target = await resolve_session_target(
                factory,
                guild_id=str(interaction.guild_id),
                user_id=str(interaction.user.id),
                session_ref=session,
            )
            if target is None:
                await interaction.followup.send(
                    "No matching session found. Provide a session id/name or "
                    "start a session first.",
                    ephemeral=True,
                )
                return
            if not await is_session_owner_or_admin(
                factory,
                session_id=str(target["session_id"]),
                guild_id=str(interaction.guild_id),
                user_id=str(interaction.user.id),
                member_roles=[str(r.id) for r in getattr(interaction.user, "roles", [])],
                member_role_names=[r.name for r in getattr(interaction.user, "roles", [])],
            ):
                await interaction.followup.send(
                    "Only the session owner or a guild admin can resume this session.",
                    ephemeral=True,
                )
                return
            resumed = await resume_session(
                factory,
                session_id=str(target["session_id"]),
                guild_id=str(interaction.guild_id),
                host_id=str(target["host_id"]),
            )
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.followup.send(
            "Resume requested." if resumed else "Session cannot be resumed.",
            ephemeral=True,
        )

    @app_commands.command(name="restart", description="Restart a session")
    async def restart(self, interaction: discord.Interaction, session: str | None = None) -> None:
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild_id is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            from discode.bot.session_service import (
                is_session_owner_or_admin,
                resolve_session_target,
                restart_session,
            )

            target = await resolve_session_target(
                factory,
                guild_id=str(interaction.guild_id),
                user_id=str(interaction.user.id),
                session_ref=session,
            )
            if target is None:
                await interaction.followup.send(
                    "No matching session found. Provide a session id/name or "
                    "start a session first.",
                    ephemeral=True,
                )
                return
            if not await is_session_owner_or_admin(
                factory,
                session_id=str(target["session_id"]),
                guild_id=str(interaction.guild_id),
                user_id=str(interaction.user.id),
                member_roles=[str(r.id) for r in getattr(interaction.user, "roles", [])],
                member_role_names=[r.name for r in getattr(interaction.user, "roles", [])],
            ):
                await interaction.followup.send(
                    "Only the session owner or a guild admin can restart this session.",
                    ephemeral=True,
                )
                return
            new_session_id = await restart_session(
                factory,
                session_id=str(target["session_id"]),
                guild_id=str(interaction.guild_id),
                requested_by_id=str(interaction.user.id),
                deployment_secret_key=settings.deployment_secret_key,
            )
        except ValueError as exc:
            await interaction.followup.send(f"Failed to restart session: {exc}", ephemeral=True)
            return
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.followup.send(
            f"Restart queued as `{new_session_id}`.",
            ephemeral=True,
        )

    @app_commands.command(name="invite", description="Invite a member to a session")
    async def invite(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        session: str | None = None,
    ) -> None:
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild_id is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            from discode.bot.policies.admin_auth import is_guild_admin_authorized
            from discode.bot.session_service import (
                invite_member,
                resolve_session_target,
            )
            from discode.db.models import Session as SessionModel

            target = await resolve_session_target(
                factory,
                guild_id=str(interaction.guild_id),
                user_id=str(interaction.user.id),
                session_ref=session,
            )
            if target is None:
                await interaction.followup.send(
                    "No matching session found. Provide a session id/name or "
                    "start a session first.",
                    ephemeral=True,
                )
                return

            member_roles = [str(r.id) for r in getattr(interaction.user, "roles", [])]
            member_role_names = [r.name for r in getattr(interaction.user, "roles", [])]
            async with factory() as db:
                sess_row = await db.get(SessionModel, target["session_id"])
                is_owner = sess_row is not None and sess_row.owner_id == str(interaction.user.id)
            if not is_owner:
                async with factory() as db:
                    is_admin = await is_guild_admin_authorized(
                        db,
                        guild_id=str(interaction.guild_id),
                        member_roles=member_roles,
                        member_role_names=member_role_names,
                    )
                if not is_admin:
                    await interaction.followup.send(
                        "Only the session owner or a guild admin can invite members.",
                        ephemeral=True,
                    )
                    return

            invited = await invite_member(
                factory,
                session_id=str(target["session_id"]),
                guild_id=str(interaction.guild_id),
                invitee_id=str(user.id),
                invitee_username=str(user),
            )
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.followup.send(
            f"{user.mention} invited." if invited else "Invite failed.",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# /help — auto-discovered command listing
# ---------------------------------------------------------------------------


# Permission tags for commands gated inside their handler bodies.
# Keys are the full path tuple (group_name, ..., leaf_name); values are the
# tag string rendered as " [<tag>]" at the end of the help line.
# Untagged commands render without a tag — fail-open is correct (a wrong tag
# would be misleading; absence just means "no special gate").
_HELP_PERM_TAGS: dict[tuple[str, ...], str] = {
    ("root", "alias", "set"): "admin",
    ("root", "alias", "remove"): "admin",
    ("root", "add"): "admin if alias",
    ("tool", "register"): "admin",
    ("tool", "update"): "admin",
    ("tool", "enable"): "admin",
    ("tool", "disable"): "admin",
    ("tool", "remove"): "admin",
    ("setup", "map", "set"): "admin",
    ("setup", "map", "remove"): "admin",
    ("setup", "admin-role", "set"): "admin",
    ("session", "config", "set"): "admin",
    ("session", "config", "get"): "admin",
    ("session", "config", "clear"): "admin",
    ("session", "config", "clear-all"): "admin",
    ("invite",): "owner+",
    ("stop",): "owner+",
    ("archive",): "owner+",
    ("resume",): "owner+",
    ("restart",): "owner+",
    ("send",): "member+",
}


def _walk_commands(tree: Any) -> list[dict[str, Any]]:
    """Flatten the app_commands tree into a list of leaf descriptors.

    Returns: list of {"path": tuple[str, ...], "args": str, "description": str}.
    """
    result: list[dict[str, Any]] = []
    for cmd in tree.get_commands():
        _walk_recursive(cmd, path=[], out=result)
    return result


def _walk_recursive(cmd: Any, *, path: list[str], out: list[dict[str, Any]]) -> None:
    full_path = [*path, cmd.name]
    if isinstance(cmd, app_commands.Group):
        for child in cmd.commands:
            _walk_recursive(child, path=full_path, out=out)
        return
    params = []
    for p in cmd.parameters:
        params.append(f"<{p.name}>" if p.required else f"[{p.name}]")
    out.append(
        {
            "path": tuple(full_path),
            "args": " ".join(params),
            "description": cmd.description or "",
        }
    )


_HELP_DESC_MAX = 60
_HELP_CHUNK_LIMIT = 1900  # Discord cap is 2000; reserve room for code-block delimiters


def _truncate(s: str, limit: int = _HELP_DESC_MAX) -> str:
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _format_leaf_line(entry: dict[str, Any], indent: str) -> str:
    """Format a single leaf as `<indent><name> <args>    <desc> [tag]`."""
    name = entry["path"][-1]
    args = entry["args"]
    head = f"{indent}{name} {args}".rstrip()
    desc = _truncate(entry["description"])
    tag = _HELP_PERM_TAGS.get(entry["path"], "")
    tag_part = f"  [{tag}]" if tag else ""
    return f"{head}    {desc}{tag_part}"


def _render_help_text(tree: Any) -> list[str]:
    """Render the full /help output, split into ≤_HELP_CHUNK_LIMIT-char chunks.

    Each chunk is wrapped in a Discord code block. Splits at group boundaries
    so a single group's leaves stay together.
    """
    leaves = _walk_commands(tree)

    top_level = [e for e in leaves if len(e["path"]) == 1]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for e in leaves:
        if len(e["path"]) == 1:
            continue
        grouped.setdefault(e["path"][0], []).append(e)

    sections: list[tuple[str | None, list[str]]] = []

    if top_level:
        body = [
            _format_leaf_line(e, indent="/") for e in sorted(top_level, key=lambda e: e["path"])
        ]
        sections.append((None, body))

    for group_name in sorted(grouped):
        children = sorted(grouped[group_name], key=lambda e: e["path"])
        body = [f"/{group_name}"]
        for e in children:
            mid_path = e["path"][1:-1]
            indent = "  " + (".".join(mid_path) + " " if mid_path else "")
            body.append(_format_leaf_line(e, indent=indent))
        sections.append((group_name, body))

    HEADER = "Discode commands\n"
    WRAPPER_OVERHEAD = len("```\n") + len("\n```")
    chunks: list[str] = []
    current_lines: list[str] = [HEADER.rstrip()]
    current_len = len(current_lines[0]) + WRAPPER_OVERHEAD

    for _name, body in sections:
        section_text = "\n".join(body)
        section_len = len(section_text) + 2  # newlines between sections

        if current_len + section_len > _HELP_CHUNK_LIMIT and len(current_lines) > 1:
            chunks.append("```\n" + "\n\n".join(current_lines) + "\n```")
            current_lines = []
            current_len = WRAPPER_OVERHEAD

        current_lines.append(section_text)
        current_len += section_len

    if current_lines:
        chunks.append("```\n" + "\n\n".join(current_lines) + "\n```")

    return chunks


# ---------------------------------------------------------------------------
# /session config helpers
# ---------------------------------------------------------------------------


async def _resolve_session_for_channel(session, channel_id: str):
    """Resolve a Discord channel/thread id to its Session row, or None."""
    from sqlalchemy import select

    from discode.db.models import Session as SessionModel

    return await session.scalar(select(SessionModel).where(SessionModel.thread_id == channel_id))


def _session_key_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    from discode.bot.session_config_service import _RESERVED_KEYS

    return [
        app_commands.Choice(name=k, value=k) for k in _RESERVED_KEYS if current.lower() in k.lower()
    ][:25]


# ---------------------------------------------------------------------------
# /session config subgroup
# ---------------------------------------------------------------------------


class SessionConfigSubgroup(
    app_commands.Group,
    name="config",
    description="Per-session model / base_url / api_key overrides",
):
    @app_commands.command(name="set", description="Set an override for this session")
    @app_commands.describe(
        key="Key: model, base_url, or api_key",
        value="Override value (api_key is encrypted at rest)",
    )
    async def set_(self, interaction: discord.Interaction, key: str, value: str) -> None:
        from discode.bot.session_config_service import set_session_config
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        member = interaction.user
        member_roles = [str(r.id) for r in getattr(member, "roles", [])]
        member_role_names = [r.name for r in getattr(member, "roles", [])]

        engine = get_shared_engine(settings.database_url)
        try:
            factory = make_session_factory(engine)
            async with factory() as session:
                sess_row = await _resolve_session_for_channel(session, str(interaction.channel_id))
                if sess_row is None:
                    await interaction.response.send_message(
                        "This command must be run inside a Discode session thread.",
                        ephemeral=True,
                    )
                    return
                result = await set_session_config(
                    session,
                    session_id=sess_row.id,
                    key=key,
                    value=value,
                    member_roles=member_roles,
                    member_role_names=member_role_names,
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request
        await interaction.response.send_message(result.message, ephemeral=True)

    @set_.autocomplete("key")
    async def _set_key_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return _session_key_autocomplete(interaction, current)

    @app_commands.command(name="get", description="Show overrides set on this session")
    async def get(self, interaction: discord.Interaction) -> None:
        from discode.bot.policies.admin_auth import is_guild_admin_authorized
        from discode.bot.session_config_service import get_session_config
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        member = interaction.user
        member_roles = [str(r.id) for r in getattr(member, "roles", [])]
        member_role_names = [r.name for r in getattr(member, "roles", [])]

        engine = get_shared_engine(settings.database_url)
        try:
            factory = make_session_factory(engine)
            async with factory() as session:
                sess_row = await _resolve_session_for_channel(session, str(interaction.channel_id))
                if sess_row is None:
                    await interaction.response.send_message(
                        "This command must be run inside a Discode session thread.",
                        ephemeral=True,
                    )
                    return
                # /session config get is documented `admin` — same gate as set/clear.
                if not await is_guild_admin_authorized(
                    session,
                    guild_id=sess_row.guild_id,
                    member_roles=member_roles,
                    member_role_names=member_role_names,
                ):
                    await interaction.response.send_message(
                        "You need the configured admin role to view session config.",
                        ephemeral=True,
                    )
                    return
                result = await get_session_config(session, session_id=sess_row.id)
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request
        if not result.rows:
            await interaction.response.send_message("No overrides set.", ephemeral=True)
            return
        lines = "\n".join(f"{r.key}: {r.display_value}" for r in result.rows)
        await interaction.response.send_message(f"```\n{lines}\n```", ephemeral=True)

    @app_commands.command(name="clear", description="Clear one override key on this session")
    @app_commands.describe(key="Key: model, base_url, or api_key")
    async def clear(self, interaction: discord.Interaction, key: str) -> None:
        from discode.bot.session_config_service import clear_session_config_key
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        member = interaction.user
        member_roles = [str(r.id) for r in getattr(member, "roles", [])]
        member_role_names = [r.name for r in getattr(member, "roles", [])]

        engine = get_shared_engine(settings.database_url)
        try:
            factory = make_session_factory(engine)
            async with factory() as session:
                sess_row = await _resolve_session_for_channel(session, str(interaction.channel_id))
                if sess_row is None:
                    await interaction.response.send_message(
                        "This command must be run inside a Discode session thread.",
                        ephemeral=True,
                    )
                    return
                result = await clear_session_config_key(
                    session,
                    session_id=sess_row.id,
                    key=key,
                    member_roles=member_roles,
                    member_role_names=member_role_names,
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request
        await interaction.response.send_message(result.message, ephemeral=True)

    @clear.autocomplete("key")
    async def _clear_key_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return _session_key_autocomplete(interaction, current)

    @app_commands.command(name="clear-all", description="Clear all overrides on this session")
    async def clear_all(self, interaction: discord.Interaction) -> None:
        from discode.bot.session_config_service import clear_all_session_config
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        member = interaction.user
        member_roles = [str(r.id) for r in getattr(member, "roles", [])]
        member_role_names = [r.name for r in getattr(member, "roles", [])]

        engine = get_shared_engine(settings.database_url)
        try:
            factory = make_session_factory(engine)
            async with factory() as session:
                sess_row = await _resolve_session_for_channel(session, str(interaction.channel_id))
                if sess_row is None:
                    await interaction.response.send_message(
                        "This command must be run inside a Discode session thread.",
                        ephemeral=True,
                    )
                    return
                result = await clear_all_session_config(
                    session,
                    session_id=sess_row.id,
                    member_roles=member_roles,
                    member_role_names=member_role_names,
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request
        await interaction.response.send_message(result.message, ephemeral=True)


# ---------------------------------------------------------------------------
# /send, /stop, /diff standalone commands
# ---------------------------------------------------------------------------


def build_send_command() -> Any:
    @app_commands.command(name="send", description="Send text input to a session")
    async def send(
        interaction: discord.Interaction,
        text: str,
        session: str | None = None,
    ) -> None:
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild_id is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return
        content = text.strip()
        if not content:
            await interaction.response.send_message("Text is required.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            from discode.bot.policies.admin_auth import is_guild_admin_authorized
            from discode.bot.session_service import (
                is_session_member,
                resolve_session_target,
                send_input_for_session,
            )

            target = await resolve_session_target(
                factory,
                guild_id=str(interaction.guild_id),
                user_id=str(interaction.user.id),
                session_ref=session,
            )
            if target is None:
                await interaction.followup.send(
                    "No matching session found. Provide a session id/name or "
                    "start a session first.",
                    ephemeral=True,
                )
                return

            member_roles = [str(r.id) for r in getattr(interaction.user, "roles", [])]
            member_role_names = [r.name for r in getattr(interaction.user, "roles", [])]
            async with factory() as db:
                is_member = await is_session_member(
                    db,
                    session_id=str(target["session_id"]),
                    user_id=str(interaction.user.id),
                )
            if not is_member:
                async with factory() as db:
                    is_admin = await is_guild_admin_authorized(
                        db,
                        guild_id=str(interaction.guild_id),
                        member_roles=member_roles,
                        member_role_names=member_role_names,
                    )
                if not is_admin:
                    await interaction.followup.send(
                        "Only session members or guild admins can /send into a session.",
                        ephemeral=True,
                    )
                    return

            try:
                key = await send_input_for_session(
                    factory,
                    session_id=str(target["session_id"]),
                    guild_id=str(interaction.guild_id),
                    thread_id=str(target["thread_id"]),
                    host_id=str(target["host_id"]),
                    owner_id=str(target["owner_id"]),
                    text_content=content,
                    enable_message_content=False,
                )
            except ValueError as exc:
                await interaction.followup.send(f"Failed to send input: {exc}", ephemeral=True)
                return
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.followup.send(f"Input queued (key={key[:8]}).", ephemeral=True)

    return send


def build_stop_command() -> Any:
    @app_commands.command(name="stop", description="Stop a session")
    async def stop(interaction: discord.Interaction, session: str | None = None) -> None:
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild_id is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            from discode.bot.session_service import (
                is_session_owner_or_admin,
                resolve_session_target,
                stop_session,
            )

            target = await resolve_session_target(
                factory,
                guild_id=str(interaction.guild_id),
                user_id=str(interaction.user.id),
                session_ref=session,
            )
            if target is None:
                await interaction.followup.send(
                    "No matching session found. Provide a session id/name or "
                    "start a session first.",
                    ephemeral=True,
                )
                return
            if not await is_session_owner_or_admin(
                factory,
                session_id=str(target["session_id"]),
                guild_id=str(interaction.guild_id),
                user_id=str(interaction.user.id),
                member_roles=[str(r.id) for r in getattr(interaction.user, "roles", [])],
                member_role_names=[r.name for r in getattr(interaction.user, "roles", [])],
            ):
                await interaction.followup.send(
                    "Only the session owner or a guild admin can stop this session.",
                    ephemeral=True,
                )
                return
            stopped = await stop_session(
                factory,
                session_id=str(target["session_id"]),
                guild_id=str(interaction.guild_id),
                requested_by_id=str(interaction.user.id),
                host_id=str(target.get("host_id") or ""),
            )
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.followup.send(
            "Stop requested." if stopped else "Session is not stoppable.",
            ephemeral=True,
        )

    return stop


def build_diff_command() -> Any:
    @app_commands.command(name="diff", description="Show a git diff preview for a session")
    async def diff(interaction: discord.Interaction, session: str | None = None) -> None:
        from discode.bot.diff_service import fetch_diff_preview
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        if interaction.guild_id is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            from discode.bot.session_service import (
                resolve_session_target,
            )

            target = await resolve_session_target(
                factory,
                guild_id=str(interaction.guild_id),
                user_id=str(interaction.user.id),
                session_ref=session,
            )
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        if target is None:
            await interaction.followup.send(
                "No matching session found. Provide a session id/name or start a session first.",
                ephemeral=True,
            )
            return

        cwd = target.get("cwd")
        if not isinstance(cwd, str) or not cwd.strip():
            await interaction.followup.send("Session has no working directory.", ephemeral=True)
            return

        try:
            preview = await fetch_diff_preview(cwd)
        except ValueError as exc:
            await interaction.followup.send(f"Failed to preview diff: {exc}", ephemeral=True)
            return

        patch = preview.patch.strip()
        if not patch:
            await interaction.followup.send("No local changes.", ephemeral=True)
            return

        max_patch_chars = 1750
        was_clipped = len(patch) > max_patch_chars
        bounded_patch = patch[:max_patch_chars]
        suffix = ""
        if preview.truncated or was_clipped:
            suffix = "\n\n(Truncated diff preview.)"
        await interaction.followup.send(
            f"```diff\n{bounded_patch}\n```{suffix}",
            ephemeral=True,
        )

    return diff


def build_help_command() -> Any:
    @app_commands.command(name="help", description="List all available Discode commands")
    async def help_(interaction: discord.Interaction) -> None:
        chunks = _render_help_text(interaction.client.tree)
        if not chunks:
            await interaction.response.send_message("No commands registered.", ephemeral=True)
            return
        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    return help_


# ---------------------------------------------------------------------------
# /tool command group
# ---------------------------------------------------------------------------


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
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.response.send_message(result.message, ephemeral=True)

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

        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                result = await list_tools(session)
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        if not result.tools:
            await interaction.response.send_message("No tools registered.", ephemeral=True)
            return

        lines = [
            f"{t.name} • {t.display_name} • {'enabled' if t.enabled else 'disabled'}"
            for t in result.tools[:25]
        ]
        await interaction.response.send_message(f"```\n{chr(10).join(lines)}\n```", ephemeral=True)

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

        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                result = await show_tool(session, name=name)
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        if result.tool is None:
            await interaction.response.send_message(result.message, ephemeral=True)
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
        await interaction.response.send_message(f"```\n{chr(10).join(fields)}\n```", ephemeral=True)

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
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.response.send_message(result.message, ephemeral=True)

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
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.response.send_message(result.message, ephemeral=True)

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
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.response.send_message(result.message, ephemeral=True)

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
                )
                await session.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.response.send_message(result.message, ephemeral=True)


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


# ---------------------------------------------------------------------------
# /start command
# ---------------------------------------------------------------------------


class StartCommand(app_commands.Command):
    """Placeholder — the real /start is registered directly on the tree."""


async def _start_handler(
    interaction: discord.Interaction,
    tool: str,
    name: str,
    cwd: str,
    runner_host_id: str | None = None,
) -> None:
    """Core logic for the /start slash command.

    Flow
    ----
    1. Resolve mapped channel via ``resolve_tool_channel``.
       If no mapping → fail immediately (no session created).
    2. Create a Discord thread in the mapped channel.
       If thread creation fails → fail before session creation.
    3. Run the create-session saga.
    4. Bind thread_id to the session row + outbox payload.
       If bind fails → mark session ``thread_bind_failed``; return degraded
       success so the user isn't left waiting.
    """
    from discode.bot.policies.tool_channel_map import resolve_tool_channel
    from discode.bot.sagas.create import run_create_saga
    from discode.bot.session_service import bind_session_thread
    from discode.config import settings
    from discode.db.engine import get_shared_engine, make_session_factory

    if interaction.guild is None or interaction.user is None:
        await interaction.response.send_message(
            "This command must be used inside a server.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    guild_id = str(interaction.guild.id)
    owner_id = str(interaction.user.id)
    owner_username = getattr(interaction.user, "name", str(interaction.user.id))

    # Determine runner host: use provided value or fall back to first online host.
    host_id = runner_host_id
    if not host_id:
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            from sqlalchemy import text as sa_text

            async with factory() as db:
                result = await db.execute(
                    sa_text("SELECT id FROM runner_hosts WHERE status = 'online' LIMIT 1")
                )
                row = result.fetchone()
                host_id = str(row[0]) if row else None
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

    if not host_id:
        await interaction.followup.send(
            "No online runner available. Ask a platform admin to bring a runner online.",
            ephemeral=True,
        )
        return

    # Step 1: Resolve tool→channel mapping.
    engine = get_shared_engine(settings.database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as db:
            channel_id = await resolve_tool_channel(db, guild_id, tool)
    finally:
        pass  # shared engine: pooled for bot lifetime, not disposed per-request

    if channel_id is None:
        await interaction.followup.send(
            f"No channel mapped for tool `{tool}`. Run `/setup map set {tool} #channel` first.",
            ephemeral=True,
        )
        return

    # Step 2: Create a Discord thread in the mapped channel.
    target_channel = interaction.guild.get_channel(int(channel_id))
    if target_channel is None or not isinstance(target_channel, discord.TextChannel):
        await interaction.followup.send(
            f"Mapped channel <#{channel_id}> is not accessible. "
            "Update the mapping with `/setup map set`.",
            ephemeral=True,
        )
        return

    try:
        thread = await target_channel.create_thread(
            name=f"{tool}: {name}",
            type=discord.ChannelType.public_thread,
        )
    except discord.HTTPException as exc:
        logger.exception("Failed to create Discord thread for /start", exc_info=exc)
        await interaction.followup.send(
            "Failed to create a Discord thread. "
            "Check bot permissions in the mapped channel and try again.",
            ephemeral=True,
        )
        return

    thread_id = str(thread.id)

    # Step 3: Run the create-session saga.
    engine = get_shared_engine(settings.database_url)
    factory = make_session_factory(engine)
    try:
        from discode.bot.setup_service import resolve_alias_or_path

        try:
            resolved_cwd = await resolve_alias_or_path(
                factory,
                guild_id=guild_id,
                raw_cwd=cwd,
            )
        except ValueError as exc:
            await interaction.followup.send(
                f"Couldn't resolve `{cwd}`: {exc}. "
                "Run `/root alias list` to see available aliases.",
                ephemeral=True,
            )
            return

        saga_result = await run_create_saga(
            factory,
            guild_id=guild_id,
            owner_id=owner_id,
            owner_username=owner_username,
            tool=tool,
            name=name,
            cwd=resolved_cwd,
            cwd_realpath=resolved_cwd,
            parent_channel_id=channel_id,
            runner_host_id=host_id,
            deployment_secret_key=settings.deployment_secret_key,
            thread_id=thread_id,
        )
    except ValueError as exc:
        # Saga rejected the request (validation / state). Best-effort thread cleanup.
        try:
            await thread.delete()
        except discord.HTTPException:
            pass
        await interaction.followup.send(f"Could not start session: {exc}", ephemeral=True)
        return
    finally:
        pass  # shared engine: pooled for bot lifetime, not disposed per-request

    # Step 4: Bind thread_id to session + outbox payload.
    engine = get_shared_engine(settings.database_url)
    factory = make_session_factory(engine)
    try:
        bind_result = await bind_session_thread(
            factory,
            session_id=saga_result.session_id,
            thread_id=thread_id,
        )
    finally:
        pass  # shared engine: pooled for bot lifetime, not disposed per-request

    if not bind_result.success:
        # Degraded success: session exists but thread binding failed.
        # Mark the session and let a repair job fix it later.
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            import uuid as _uuid

            from sqlalchemy import text as sa_text

            async with factory() as db:
                await db.execute(
                    sa_text(
                        "UPDATE sessions SET status_reason = 'thread_bind_failed' WHERE id = :sid"
                    ),
                    {"sid": _uuid.UUID(saga_result.session_id)},
                )
                await db.commit()
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        await interaction.followup.send(
            f"Session `{saga_result.session_id[:8]}` started in <#{channel_id}> "
            f"(thread {thread.mention}), but the thread binding encountered an issue "
            "and will be repaired automatically.",
            ephemeral=True,
        )
        return

    if saga_result.resumed_from_session_id:
        verb = "Resumed"
    else:
        verb = "Started"

    await interaction.followup.send(
        f"{verb} session `{saga_result.session_id[:8]}` · tool `{tool}` · thread {thread.mention}",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------


class DiscodeBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

        # Per-thread typing-indicator state. The tracker maintains a pending
        # user-message counter per thread; the indicator runs while count > 0
        # and self-clears via a 360s safety timeout if a reply is ever lost.
        from discode.bot.typing_tracker import TypingTracker

        def _factory(thread_id: str):
            async def _trigger() -> None:
                channel = self.get_channel(int(thread_id))
                if channel is None:
                    return
                try:
                    await channel.trigger_typing()
                except Exception:
                    pass

            return _trigger

        self._typing_tracker = TypingTracker(trigger_factory=_factory)
        self._register_commands()

    def _register_commands(self) -> None:
        # Setup group
        self.tree.add_command(SetupGroup())
        # Root group
        self.tree.add_command(RootCommands())
        # Session group (with config subgroup)
        _session_cmds = SessionCommands()
        _session_cmds.add_command(SessionConfigSubgroup())
        self.tree.add_command(_session_cmds)
        # Tool group
        self.tree.add_command(ToolCommands())
        # Standalone commands
        start_cmd = app_commands.Command(
            name="start",
            description="Start a new coding session",
            callback=_start_handler,
        )
        start_cmd.autocomplete("tool")(_start_tool_autocomplete)
        self.tree.add_command(start_cmd)
        self.tree.add_command(build_send_command())
        self.tree.add_command(build_stop_command())
        self.tree.add_command(build_diff_command())
        self.tree.add_command(build_help_command())

    async def setup_hook(self) -> None:
        pass

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        logger.exception("app command error", exc_info=error)
        message = "Command failed. Check service logs and try again."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
            return
        await interaction.response.send_message(message, ephemeral=True)

    async def close(self) -> None:
        await super().close()

    async def on_ready(self) -> None:
        logger.info("bot ready: %s", self.user)
        # Reconcile command scopes once on startup (idempotent).
        try:
            from discode.bot.command_sync import reconcile_commands

            guild_ids = [g.id for g in self.guilds]
            report = await reconcile_commands(self, guild_ids)
            logger.info(
                "command_sync on_ready complete",
                extra={
                    "global_added": report.global_added,
                    "global_updated": report.global_updated,
                    "global_removed": report.global_removed,
                    "guild_added": report.guild_added,
                    "guild_updated": report.guild_updated,
                    "guild_removed": report.guild_removed,
                    "timestamp": report.timestamp,
                },
            )
        except Exception:
            logger.exception("command_sync on_ready failed — commands may be stale")

    async def on_message(self, message: discord.Message) -> None:
        """Route direct thread messages as session input.

        Silently ignores:
        - Bot or webhook messages (prevents loops and noise)
        - Messages in non-thread channels
        - Messages in threads not bound to any active session

        Rejects with a reply if the sender is not the session owner/member.
        Enqueues ``runner.send_input.v1`` for authorized messages.
        """
        logger.info(
            "on_message: ch=%s author=%s bot=%s wh=%s",
            getattr(message.channel, "id", "?"),
            getattr(message.author, "id", "?"),
            getattr(message.author, "bot", "?"),
            message.webhook_id,
        )
        # Defensive: tests sometimes construct DiscodeBot via object.__new__
        # which bypasses __init__. Lazy-init the tracker if missing.
        if not hasattr(self, "_typing_tracker"):
            from discode.bot.typing_tracker import TypingTracker

            def _factory(thread_id: str):
                async def _trigger() -> None:
                    channel = self.get_channel(int(thread_id))
                    if channel is None:
                        return
                    try:
                        await channel.trigger_typing()
                    except Exception:
                        pass

                return _trigger

            self._typing_tracker = TypingTracker(trigger_factory=_factory)

        # Self-detect: if this is *our own* message landing in a thread,
        # decrement the typing-tracker counter. Must run BEFORE the
        # bot/webhook early-return below, otherwise the bot's own posts
        # would be filtered out and the indicator would never clear.
        try:
            self_user_id = self.user.id if self.user is not None else None
        except AttributeError:
            self_user_id = None
        if (
            self_user_id is not None
            and message.author.id == self_user_id
            and isinstance(message.channel, discord.Thread)
        ):
            await self._typing_tracker.note_bot_message(str(message.channel.id))
            return

        # 1. Ignore bots and webhooks (fast-path, no imports needed).
        if message.author.bot or message.webhook_id is not None:
            logger.info("on_message: drop reason=bot_or_webhook")
            return

        # 2. Ignore non-thread channels.
        if not isinstance(message.channel, discord.Thread):
            logger.info(
                "on_message: drop reason=non_thread channel_type=%s",
                type(message.channel).__name__,
            )
            return

        thread_id = str(message.channel.id)
        guild_id = str(message.guild.id) if message.guild else None
        if guild_id is None:
            logger.info("on_message: drop reason=no_guild thread=%s", thread_id)
            return

        # Deferred imports (after early exits to avoid import cost on hot path).
        from discode.bot.sagas.input import run_input_saga
        from discode.bot.session_service import is_session_member, resolve_session_for_thread
        from discode.config import settings
        from discode.db.engine import get_shared_engine, make_session_factory

        # 3. Feature flag: message-content input must be explicitly enabled.
        if not settings.ENABLE_MESSAGE_CONTENT_INPUT:
            logger.info("on_message: drop reason=feature_flag_off thread=%s", thread_id)
            return

        # 3. Look up the session bound to this thread.
        engine = get_shared_engine(settings.database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as db:
                session_row = await resolve_session_for_thread(
                    db, thread_id=thread_id, guild_id=guild_id
                )
                if session_row is None:
                    logger.info(
                        "on_message: drop reason=no_session_for_thread thread=%s guild=%s",
                        thread_id,
                        guild_id,
                    )
                    return

                session_id = str(session_row.id)
                host_id = session_row.host_id
                owner_id = session_row.owner_id or ""
                logger.info(
                    "on_message: resolved session sid=%s thread=%s status=%s",
                    session_id[:8],
                    thread_id,
                    getattr(session_row, "status", "?"),
                )

                # Gate: don't enqueue input for sessions that are no longer active.
                _inactive_statuses = ("stopped", "archived", "failed", "orphaned")
                if getattr(session_row, "status", None) in _inactive_statuses:
                    logger.info(
                        "on_message: drop reason=session_inactive sid=%s status=%s",
                        session_id[:8],
                        session_row.status,
                    )
                    return

                # 4. Check membership.
                sender_id = str(message.author.id)
                authorized = await is_session_member(db, session_id=session_id, user_id=sender_id)
                logger.info(
                    "on_message: membership sid=%s sender=%s authorized=%s",
                    session_id[:8],
                    sender_id,
                    authorized,
                )

            if not authorized:
                await message.reply(
                    "You're not a member of this session. "
                    "Ask the owner to invite you with `/session invite`."
                )
                return

            # 5. Track this turn for the typing indicator. The tracker
            # increments a per-thread counter; the indicator stays on while
            # any pending user message is unreplied. Self-detect (above)
            # decrements when the bot's reply lands.
            await self._typing_tracker.note_user_message(thread_id)

            # 6. Enqueue the input via the saga (idempotent outbox write).
            logger.info(
                "on_message: enqueuing sid=%s text_len=%d",
                session_id[:8],
                len(message.content or ""),
            )
            try:
                await run_input_saga(
                    factory,
                    session_id=session_id,
                    guild_id=guild_id,
                    thread_id=thread_id,
                    host_id=host_id or "",
                    owner_id=owner_id,
                    text_content=message.content,
                )
                logger.info("on_message: enqueued sid=%s", session_id[:8])
            except ValueError as exc:
                # Saga failed before any reply will land — decrement indicator.
                await self._typing_tracker.note_bot_message(thread_id)
                reason = str(exc)
                if reason == "empty_input":
                    return  # Silently drop empty messages (e.g. image-only)
                logger.warning(
                    "on_message: input saga rejected message",
                    extra={"session_id": session_id, "reason": reason},
                )
                await message.reply(f"Could not send input: {reason}")
                return
            except Exception:
                # Same: no reply will land, so self-detect can't clear it.
                await self._typing_tracker.note_bot_message(thread_id)
                logger.exception(
                    "on_message: unexpected error enqueuing input",
                    extra={"session_id": session_id},
                )
                return
        finally:
            pass  # shared engine: pooled for bot lifetime, not disposed per-request

        # 6. Optional lightweight acknowledgement.
        try:
            await message.add_reaction("✅")
        except discord.HTTPException:
            pass  # Best-effort; don't fail the whole handler.


def main() -> None:
    import os

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    async def _run() -> None:
        from discode.runtime import AppRuntime

        bot = DiscodeBot()
        token = os.environ.get("DISCORD_TOKEN", "")
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        runtime = AppRuntime.from_env()
        relay = RelayLoop(
            producer="bot",
            redis_client=runtime.redis,
            db_factory=runtime.db_factory,
        )

        def _request_stop() -> None:
            if not stop_event.is_set():
                stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _request_stop)

        from discode.janitor.main import supervise_relay

        bot_task = asyncio.create_task(bot.start(token), name="discord-bot")
        # Supervise the relay so a transient crash restarts it (with backoff)
        # instead of silently leaving outbox rows unpublished forever.
        relay_task = asyncio.create_task(supervise_relay(relay), name="bot-relay")
        stop_task = asyncio.create_task(stop_event.wait(), name="stop-event")
        done, _pending = await asyncio.wait(
            {bot_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if stop_task in done and not bot_task.done():
            logger.info("shutdown signal received; closing bot")
            await bot.close()
            bot_task.cancel()
            relay_task.cancel()
            await asyncio.gather(bot_task, relay_task, return_exceptions=True)
        else:
            stop_task.cancel()
            relay_task.cancel()
            await asyncio.gather(stop_task, relay_task, return_exceptions=True)

        await runtime.close()
        from discode.db.engine import dispose_shared_engines

        await dispose_shared_engines()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("bot stopped")
