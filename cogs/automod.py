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

# ---- Brand colors ----
COLOR_WARN = 0xF2A93B      # amber — a warning was issued
COLOR_DANGER = 0xE0405A    # red — ban / hard failure
COLOR_SUCCESS = 0x57C785   # green — cleared / all good
COLOR_NEUTRAL = 0x5865F2   # blurple — informational

FOOTER_ICON = "https://cdn-icons-png.flaticon.com/512/564/564619.png"
FOOTER_TEXT = "AutoMod"


# ================================
#   Visual helpers
# ================================

def _base_embed(color: int) -> discord.Embed:
    embed = discord.Embed(color=color, timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    return embed


def warning_bar(count: int, total: int = BAN_THRESHOLD) -> str:
    """Renders something like 🟥🟥⬜ 2/3 instead of plain text."""
    filled = "🟥" * min(count, total)
    empty = "⬜" * max(total - count, 0)
    return f"{filled}{empty}  `{count}/{total}`"


def is_staff(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    perms = interaction.user.guild_permissions
    role_ids = [r.id for r in interaction.user.roles]
    return perms.administrator or any(rid in EXTRA_ALLOWED_ROLE_IDS for rid in role_ids)


def permission_denied_embed() -> discord.Embed:
    embed = _base_embed(COLOR_DANGER)
    embed.description = "🔒 **You don't have permission to use this command.**"
    return embed


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
            return await interaction.response.send_message(embed=permission_denied_embed(), ephemeral=True)
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        if member.bot:
            embed = _base_embed(COLOR_DANGER)
            embed.description = "🤖 **Cannot warn bots.**"
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if member == interaction.guild.owner:
            embed = _base_embed(COLOR_DANGER)
            embed.description = "👑 **Cannot warn the server owner.**"
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.defer(ephemeral=False)

        saved = await db.add_warning(
            guild_id=interaction.guild.id,
            target_id=member.id,
            moderator_id=interaction.user.id,
            reason=reason,
        )
        if not saved:
            embed = _base_embed(COLOR_DANGER)
            embed.title = "⚠️ Couldn't save warning"
            embed.description = (
                "The database isn't configured or is unreachable, "
                "so this warning was **not recorded**."
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
            dm_embed = _base_embed(COLOR_WARN)
            dm_embed.title = "⚠️ You've received a warning"
            dm_embed.description = f"You were warned in **{interaction.guild.name}**."
            dm_embed.add_field(name="Reason", value=reason or "*No reason provided*", inline=False)
            dm_embed.add_field(name="Warning level", value=warning_bar(count), inline=False)
            dm_embed.add_field(
                name="Good to know",
                value=f"Warnings automatically expire **{WARNING_EXPIRY_DAYS} days** "
                      f"after they're given if you don't receive another one.",
                inline=False,
            )
            if interaction.guild.icon:
                dm_embed.set_thumbnail(url=interaction.guild.icon.url)
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            dm_sent = False

        if count >= BAN_THRESHOLD:
            me = interaction.guild.me
            if not me or not me.guild_permissions.ban_members:
                embed = _base_embed(COLOR_DANGER)
                embed.title = "⚠️ Ban threshold reached — action blocked"
                embed.set_author(name=str(member), icon_url=member.display_avatar.url)
                embed.description = (
                    f"{member.mention} reached the warning threshold, but I'm missing the "
                    f"**Ban Members** permission, so no action was taken automatically."
                )
                embed.add_field(name="Warning level", value=warning_bar(count), inline=False)
                return await interaction.followup.send(embed=embed)

            if member.top_role >= me.top_role:
                embed = _base_embed(COLOR_DANGER)
                embed.title = "⚠️ Ban threshold reached — action blocked"
                embed.set_author(name=str(member), icon_url=member.display_avatar.url)
                embed.description = (
                    f"{member.mention} reached the warning threshold, but their top role is "
                    f"above or equal to mine, so no action was taken automatically."
                )
                embed.add_field(name="Warning level", value=warning_bar(count), inline=False)
                return await interaction.followup.send(embed=embed)

            try:
                await interaction.guild.ban(
                    member,
                    reason=f"Automod: reached {count} active warnings (by {interaction.user})",
                )
                log.info("AUTOMOD auto-ban: %s (3rd warning) by %s", member, interaction.user)

                embed = _base_embed(COLOR_DANGER)
                embed.title = "🔨 Member auto-banned"
                embed.set_author(name=str(member), icon_url=member.display_avatar.url)
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.description = f"{member.mention} reached the warning limit and has been **banned**."
                embed.add_field(name="Warning level", value=warning_bar(count), inline=False)
                embed.add_field(name="Final reason", value=reason or "*No reason provided*", inline=False)
                embed.add_field(name="Banned by", value=interaction.user.mention, inline=True)
                if not dm_sent:
                    embed.add_field(name="Note", value="⚠️ Couldn't DM the member before banning.", inline=True)
                return await interaction.followup.send(embed=embed)
            except discord.Forbidden:
                embed = _base_embed(COLOR_DANGER)
                embed.title = "⚠️ Ban threshold reached, but ban failed"
                embed.set_author(name=str(member), icon_url=member.display_avatar.url)
                embed.description = f"{member.mention} reached the warning limit but Discord denied the ban."
                return await interaction.followup.send(embed=embed)

        # Normal warning confirmation
        embed = _base_embed(COLOR_WARN)
        embed.title = "⚠️ Warning issued"
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Member", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # spacer to keep the row tidy
        embed.add_field(name="Reason", value=reason or "*No reason provided*", inline=False)
        embed.add_field(name="Warning level", value=warning_bar(count), inline=False)
        if not dm_sent:
            embed.add_field(name="Note", value="⚠️ Couldn't DM the member (DMs closed).", inline=False)
        await interaction.followup.send(embed=embed)

    # ---------- /warn list ----------

    @warn_group.command(name="list", description="Show a member's active warnings")
    @app_commands.describe(member="Member to check")
    async def warn_list(self, interaction: discord.Interaction, member: discord.Member):
        if not is_staff(interaction):
            return await interaction.response.send_message(embed=permission_denied_embed(), ephemeral=True)
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        active = await db.get_active_warnings(guild_id=interaction.guild.id, target_id=member.id)

        if not active:
            embed = _base_embed(COLOR_SUCCESS)
            embed.title = "✅ Clean record"
            embed.set_author(name=str(member), icon_url=member.display_avatar.url)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.description = f"{member.mention} has no active warnings."
            return await interaction.followup.send(embed=embed, ephemeral=True)

        embed = _base_embed(COLOR_WARN)
        embed.title = f"📋 Warning History — {count_label(len(active))}"
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Warning level", value=warning_bar(len(active)), inline=False)

        for i, w in enumerate(active, 1):
            reason = w.get("reason") or "*No reason provided*"
            when = f"<t:{_to_ts(w.get('created_at'))}:R>"
            embed.add_field(
                name=f"Warning #{i}",
                value=f"{reason}\n*issued {when}*",
                inline=False,
            )

        embed.set_footer(
            text=f"{FOOTER_TEXT} • Warnings auto-expire {WARNING_EXPIRY_DAYS} days after being issued",
            icon_url=FOOTER_ICON,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- /warn clear ----------

    @warn_group.command(name="clear", description="Clear all active warnings for a member")
    @app_commands.describe(member="Member to clear warnings for")
    async def warn_clear(self, interaction: discord.Interaction, member: discord.Member):
        if not is_staff(interaction):
            return await interaction.response.send_message(embed=permission_denied_embed(), ephemeral=True)
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        cleared = await db.clear_warnings(
            guild_id=interaction.guild.id, target_id=member.id, cleared_by=interaction.user.id
        )

        if cleared == 0:
            embed = _base_embed(COLOR_SUCCESS)
            embed.title = "Nothing to clear"
            embed.set_author(name=str(member), icon_url=member.display_avatar.url)
            embed.description = f"{member.mention} had no active warnings."
        else:
            log.info("AUTOMOD clear: %s cleared %s warning(s) for %s", interaction.user, cleared, member)
            embed = _base_embed(COLOR_SUCCESS)
            embed.title = "✅ Warnings cleared"
            embed.set_author(name=str(member), icon_url=member.display_avatar.url)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.description = f"Cleared **{cleared}** active {('warning' if cleared == 1 else 'warnings')} for {member.mention}."
            embed.add_field(name="Cleared by", value=interaction.user.mention, inline=True)
            embed.add_field(name="New level", value=warning_bar(0), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- error handling ----------

    @warn_add.error
    @warn_list.error
    @warn_clear.error
    async def warn_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        log.exception("AutoMod command error", exc_info=error)
        embed = _base_embed(COLOR_DANGER)
        embed.description = "⚠️ **Something went wrong.**"
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoMod(bot))