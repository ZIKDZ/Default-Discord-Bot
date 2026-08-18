import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from cogs.autorole import is_staff, AutoRole  # reuse config + permission logic

log = logging.getLogger(__name__)

# ==========================================================
# TEMP COG — delete cogs/temp_backfill_roles.py once you've run this.
# It only adds one slash command: /backfill-roles
# ==========================================================

MEMBERS_TO_CHECK = 100
DELAY_BETWEEN_EDITS = 0.6  # seconds, keeps us comfortably under role-edit rate limits


class TempBackfillRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="backfill-roles",
        description=f"(Temp) Check the last {MEMBERS_TO_CHECK} joined members and fix missing autoroles",
    )
    async def backfill_roles(self, interaction: discord.Interaction):
        if not is_staff(interaction):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        me = guild.me
        if not me or not me.guild_permissions.manage_roles:
            return await interaction.response.send_message(
                "❌ I need the **Manage Roles** permission to do that.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=True)

        # Make sure member cache is populated (needed for accurate joined_at sorting)
        if guild.chunked is False:
            try:
                await guild.chunk()
            except Exception:
                log.exception("Failed to chunk guild %s", guild.id)

        autorole_cog: AutoRole | None = self.bot.get_cog("AutoRole")
        if autorole_cog is None:
            return await interaction.followup.send(
                "❌ The AutoRole cog isn't loaded, can't read the configured roles.", ephemeral=True
            )

        cfg = autorole_cog.get_guild_config(guild.id)
        human_role = guild.get_role(int(cfg["human_role_id"])) if cfg.get("human_role_id") else None
        bot_role = guild.get_role(int(cfg["bot_role_id"])) if cfg.get("bot_role_id") else None

        if not human_role and not bot_role:
            return await interaction.followup.send(
                "❌ Neither the human nor bot role is configured/found. Run `/autorole status` first.",
                ephemeral=True,
            )

        for role, label in ((human_role, "human"), (bot_role, "bot")):
            if role and role >= me.top_role:
                return await interaction.followup.send(
                    f"❌ The {label} role ({role.mention}) is above or equal to my top role, "
                    "I can't assign it. Fix the role order first.",
                    ephemeral=True,
                )

        members = [m for m in guild.members if m.joined_at is not None]
        members.sort(key=lambda m: m.joined_at, reverse=True)
        members = members[:MEMBERS_TO_CHECK]

        checked = 0
        already_ok = 0
        fixed = 0
        skipped_no_role_configured = 0
        failed = 0

        for member in members:
            checked += 1
            target_role = bot_role if member.bot else human_role

            if not target_role:
                skipped_no_role_configured += 1
                continue

            if target_role in member.roles:
                already_ok += 1
                continue

            try:
                await member.add_roles(target_role, reason="Autorole backfill (last 100 joins)")
                fixed += 1
                log.info("Backfill: gave %s to %s in %s", target_role, member, guild)
            except discord.Forbidden:
                failed += 1
                log.warning("Backfill: forbidden adding %s to %s", target_role, member)
            except Exception:
                failed += 1
                log.exception("Backfill: failed to add role to %s", member)

            await asyncio.sleep(DELAY_BETWEEN_EDITS)

        embed = discord.Embed(
            title="🔄 Autorole Backfill Complete",
            color=0x57F287,
            description=f"Checked the {checked} most recently joined members.",
        )
        embed.add_field(name="Already correct", value=str(already_ok), inline=True)
        embed.add_field(name="Fixed", value=str(fixed), inline=True)
        embed.add_field(name="Failed", value=str(failed), inline=True)
        if skipped_no_role_configured:
            embed.add_field(
                name="Skipped (no role configured for their type)",
                value=str(skipped_no_role_configured),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TempBackfillRoles(bot))
