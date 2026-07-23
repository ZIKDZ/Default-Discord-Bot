import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core import supabase_client as db

log = logging.getLogger(__name__)

# ==========================================================
# EDIT THIS LIST ONLY
EXTRA_ALLOWED_ROLE_IDS = [
    1141838188650967200,
]
# ==========================================================

# Warnings expire (stop counting) this many days after they were given.
# This lives on the db module too (WARNING_EXPIRY_DAYS) since that's what
# actually filters the query — kept here just for display text.
WARNING_EXPIRY_DAYS = db.WARNING_EXPIRY_DAYS

# How many ACTIVE warnings triggers an automatic ban.
BAN_THRESHOLD = 3


def is_staff(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    perms = interaction.user.guild_permissions
    role_ids = [r.id for r in interaction.user.roles]
    return perms.administrator or any(rid in EXTRA_ALLOWED_ROLE_IDS for rid in role_ids)


def soft_embed(title: str, description: str, color: int = 0xF2B705) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )


class AutoMod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    warn_group = app_commands.Group(name="warn", description="Warning system (staff only)")

    # ---------- /warn add ----------

    @warn_group.command(name="add", description="Warn a member. 3 active warnings = auto-ban.")
    @app_commands.describe(member="Member to warn", reason="Reason for the warning")
    async def warn_add(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str | None = None,
    ):
        if not is_staff(interaction):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        if member.bot:
            return await interaction.response.send_message("❌ Cannot warn bots.", ephemeral=True)
        if member == interaction.guild.owner:
            return await interaction.response.send_message("❌ Cannot warn the server owner.", ephemeral=True)

        await interaction.response.defer(ephemeral=False)

        saved = await db.add_warning(
            guild_id=interaction.guild.id,
            target_id=member.id,
            moderator_id=interaction.user.id,
            reason=reason,
        )
        if not saved:
            embed = soft_embed(
                "⚠️ Couldn't save warning",
                "The database isn't configured or is unreachable, so this warning was not recorded.",
                color=0xE74C3C,
            )
            return await interaction.followup.send(embed=embed)

        active = await db.get_active_warnings(guild_id=interaction.guild.id, target_id=member.id)
        count = len(active)

        log.info(
            "AUTOMOD warn: %s -> %s (%s/%s active) reason=%r",
            interaction.user, member, count, BAN_THRESHOLD, reason,
        )

        # Notify the member (best-effort, don't fail the command if DMs are closed)
        dm_sent = True
        try:
            dm_embed = soft_embed(
                "You've received a warning",
                f"You were warned in **{interaction.guild.name}**.\n"
                f"**Reason:** {reason or 'No reason provided'}\n"
                f"**Active warnings:** {count}/{BAN_THRESHOLD}\n\n"
                f"Warnings expire automatically after {WARNING_EXPIRY_DAYS} days if you receive no new ones.",
                color=0xF2B705,
            )
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            dm_sent = False

        if count >= BAN_THRESHOLD:
            # Auto-ban
            me = interaction.guild.me
            if not me or not me.guild_permissions.ban_members:
                embed = soft_embed(
                    "⚠️ Ban threshold reached, but I can't ban",
                    f"{member.mention} reached {count} active warnings but I'm missing the "
                    f"**Ban Members** permission, so no action was taken automatically.",
                    color=0xE74C3C,
                )
                return await interaction.followup.send(embed=embed)

            if member.top_role >= me.top_role:
                embed = soft_embed(
                    "⚠️ Ban threshold reached, but I can't ban",
                    f"{member.mention} reached {count} active warnings but their top role is "
                    f"above or equal to mine, so no action was taken automatically.",
                    color=0xE74C3C,
                )
                return await interaction.followup.send(embed=embed)

            try:
                await interaction.guild.ban(
                    member,
                    reason=f"Automod: reached {count} active warnings (by {interaction.user})",
                )
                log.info("AUTOMOD auto-ban: %s (3rd warning) by %s", member, interaction.user)
                embed = soft_embed(
                    "🔨 Member auto-banned",
                    f"{member.mention} reached **{count}/{BAN_THRESHOLD}** active warnings and has been banned.",
                    color=0xE74C3C,
                )
                if not dm_sent:
                    embed.set_footer(text="Note: could not DM the member before banning (DMs closed).")
                return await interaction.followup.send(embed=embed)
            except discord.Forbidden:
                embed = soft_embed(
                    "⚠️ Ban threshold reached, but ban failed",
                    f"{member.mention} reached {count} active warnings but Discord denied the ban.",
                    color=0xE74C3C,
                )
                return await interaction.followup.send(embed=embed)

        # Normal warning confirmation
        embed = soft_embed(
            "⚠️ Warning issued",
            f"{member.mention} has been warned.\n"
            f"**Reason:** {reason or 'No reason provided'}\n"
            f"**Active warnings:** {count}/{BAN_THRESHOLD}",
            color=0xF2B705,
        )
        if not dm_sent:
            embed.set_footer(text="Note: could not DM the member (DMs closed).")
        await interaction.followup.send(embed=embed)

    # ---------- /warn list ----------

    @warn_group.command(name="list", description="Show a member's active warnings")
    @app_commands.describe(member="Member to check")
    async def warn_list(self, interaction: discord.Interaction, member: discord.Member):
        if not is_staff(interaction):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        active = await db.get_active_warnings(guild_id=interaction.guild.id, target_id=member.id)

        if not active:
            embed = soft_embed(
                "No active warnings",
                f"{member.mention} has no active warnings.",
                color=0x2ECC71,
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        lines = []
        for i, w in enumerate(active, 1):
            created = w.get("created_at", "")[:10]
            reason = w.get("reason") or "No reason provided"
            lines.append(f"**{i}.** {reason} — <t:{_to_ts(w.get('created_at'))}:R> ({created})")

        embed = soft_embed(
            f"Active warnings — {count_label(len(active))}",
            "\n".join(lines),
            color=0xF2B705,
        )
        embed.set_footer(text=f"Warnings auto-expire {WARNING_EXPIRY_DAYS} days after being issued.")
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- /warn clear ----------

    @warn_group.command(name="clear", description="Clear all active warnings for a member")
    @app_commands.describe(member="Member to clear warnings for")
    async def warn_clear(self, interaction: discord.Interaction, member: discord.Member):
        if not is_staff(interaction):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        cleared = await db.clear_warnings(
            guild_id=interaction.guild.id, target_id=member.id, cleared_by=interaction.user.id
        )

        if cleared == 0:
            embed = soft_embed(
                "Nothing to clear",
                f"{member.mention} had no active warnings.",
                color=0x2ECC71,
            )
        else:
            log.info("AUTOMOD clear: %s cleared %s warning(s) for %s", interaction.user, cleared, member)
            embed = soft_embed(
                "✅ Warnings cleared",
                f"Cleared **{cleared}** active warning(s) for {member.mention}.",
                color=0x2ECC71,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- error handling ----------

    @warn_add.error
    @warn_list.error
    @warn_clear.error
    async def warn_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        log.exception("AutoMod command error", exc_info=error)
        msg = "⚠️ Something went wrong."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


def count_label(n: int) -> str:
    return f"{n} warning" if n == 1 else f"{n} warnings"


def _to_ts(iso_str: str | None) -> int:
    """Convert a Supabase ISO timestamp to a unix timestamp for Discord's <t:...> formatting."""
    if not iso_str:
        return int(datetime.now(timezone.utc).timestamp())
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return int(datetime.now(timezone.utc).timestamp())


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoMod(bot))
