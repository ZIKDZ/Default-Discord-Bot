import logging
from datetime import datetime, timedelta, timezone

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


WARNING_EXPIRY_DAYS = db.WARNING_EXPIRY_DAYS
BAN_THRESHOLD = 3

TIMEOUT_DURATIONS: dict[int, timedelta] = {
    1: timedelta(hours=1),
    2: timedelta(days=1),
}


def format_duration(td: timedelta) -> str:
    total_seconds = int(td.total_seconds())
    if total_seconds % 86400 == 0:
        days = total_seconds // 86400
        return f"{days} day" if days == 1 else f"{days} days"
    hours = total_seconds // 3600
    return f"{hours} hour" if hours == 1 else f"{hours} hours"


# ---- Brand colors ----

COLOR_WARN    = 0xF2A93B
COLOR_DANGER  = 0xE0405A
COLOR_SUCCESS = 0x57C785
COLOR_NEUTRAL = 0x5865F2

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
    filled = "🟥" * min(count, total)
    empty  = "⬜" * max(total - count, 0)
    return f"{filled}{empty}  `{count}/{total}`"


def is_staff(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    perms    = interaction.user.guild_permissions
    role_ids = [r.id for r in interaction.user.roles]
    return perms.administrator or any(rid in EXTRA_ALLOWED_ROLE_IDS for rid in role_ids)


def permission_denied_embed() -> discord.Embed:
    embed = _base_embed(COLOR_DANGER)
    embed.description = "🚫 **You don't have permission to use this command.**"
    return embed


def count_label(n: int) -> str:
    return f"{n} warning" if n == 1 else f"{n} warnings"


def _to_ts(iso_str: str | None) -> int:
    if not iso_str:
        return int(datetime.now(timezone.utc).timestamp())
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return int(datetime.now(timezone.utc).timestamp())


# ================================

#   Timeout helper

# ================================


async def _apply_timeout(
    member: discord.Member,
    duration: timedelta,
    reason: str,
) -> tuple[bool, str | None]:
    """
    Times out *member* for *duration*.

    If the member holds the Administrator permission through one or more roles,
    those roles are temporarily stripped so Discord allows the timeout, then
    immediately restored.  The roles that were removed are logged.

    Returns:
        (success: bool, note: str | None)
        *note* is a human-readable string describing what extra steps were
        taken (e.g. which roles were removed), or None if nothing unusual happened.
    """

    # ------------------------------------------------------------------ #

    # 1. Collect every role that grants Administrator (skip @everyone).   #

    # ------------------------------------------------------------------ #

    admin_roles: list[discord.Role] = [
        r for r in member.roles
        if r.id != member.guild.id and r.permissions.administrator
    ]

    note: str | None = None

    # ------------------------------------------------------------------ #

    # 2. Strip admin roles so the timeout API call is accepted.           #

    # ------------------------------------------------------------------ #

    if admin_roles:
        try:
            await member.remove_roles(
                *admin_roles,
                reason="AutoMod: temporarily removing admin role(s) to apply timeout",
                atomic=False,
            )
            log.info(
                "AUTOMOD timeout-prep: removed admin role(s) %s from %s",
                [r.name for r in admin_roles],
                member,
            )
        except discord.Forbidden:
            return False, "Couldn't remove the member's administrator role(s) — check role hierarchy."
        except discord.HTTPException as exc:
            return False, f"Failed to remove admin role(s): {exc}"

    # ------------------------------------------------------------------ #

    # 3. Apply the actual timeout.                                        #

    # ------------------------------------------------------------------ #

    try:
        until = datetime.now(timezone.utc) + duration
        await member.timeout(until, reason=reason)
        log.info("AUTOMOD timeout: %s for %s", member, format_duration(duration))
    except discord.Forbidden:
        # Restore roles before giving up so the member isn't left role-less.

        if admin_roles:
            try:
                await member.add_roles(
                    *admin_roles,
                    reason="AutoMod: restoring admin role(s) after failed timeout",
                    atomic=False,
                )
            except discord.HTTPException:
                pass
        return False, "Discord denied the timeout request (missing permission or role hierarchy)."
    except discord.HTTPException as exc:
        if admin_roles:
            try:
                await member.add_roles(
                    *admin_roles,
                    reason="AutoMod: restoring admin role(s) after failed timeout",
                    atomic=False,
                )
            except discord.HTTPException:
                pass
        return False, f"Timeout request failed: {exc}"

    # ------------------------------------------------------------------ #

    # 4. Re-add the admin roles (Discord ignores the timeout for admins   #

    #    but the roles are legitimately theirs, so we give them back).    #

    #    The timeout will still show in audit log / be stored server-side #

    #    until they lose the admin role again, which is fine for our      #

    #    purposes of recording the moderation action.                     #

    # ------------------------------------------------------------------ #

    if admin_roles:
        try:
            await member.add_roles(
                *admin_roles,
                reason="AutoMod: restoring admin role(s) after timeout was applied",
                atomic=False,
            )
            role_names = ", ".join(f"`{r.name}`" for r in admin_roles)
            note = (
                f"⚠️ Member had administrator role(s) ({role_names}). "
                "They were temporarily removed to apply the timeout, then restored."
            )
            log.info(
                "AUTOMOD timeout-restore: re-added admin role(s) %s to %s",
                [r.name for r in admin_roles],
                member,
            )
        except discord.HTTPException as exc:
            role_names = ", ".join(f"`{r.name}`" for r in admin_roles)
            note = (
                f"⚠️ Could not restore admin role(s) ({role_names}) after timeout: {exc}. "
                "Please restore them manually."
            )
            log.warning(
                "AUTOMOD timeout-restore FAILED for %s: %s",
                member,
                exc,
            )

    return True, note


class AutoMod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    warn_group = app_commands.Group(name="warn", description="Warning system (staff only)")

    # ================================================================== #

    #   /warn add                                                         #

    # ================================================================== #


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
                embed=permission_denied_embed(), ephemeral=True
            )
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        if member.bot:
            embed = _base_embed(COLOR_DANGER)
            embed.description = "🚫 **Cannot warn bots.**"
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if member == interaction.guild.owner:
            embed = _base_embed(COLOR_DANGER)
            embed.description = "🚫 **Cannot warn the server owner.**"
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.defer(ephemeral=False)

        # ---- save to DB ------------------------------------------------ #

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

        active = await db.get_active_warnings(
            guild_id=interaction.guild.id, target_id=member.id
        )
        count = len(active)

        log.info(
            "AUTOMOD warn: %s -> %s (%s/%s active) reason=%r",
            interaction.user, member, count, BAN_THRESHOLD, reason,
        )

        # ---- DM the member (best-effort) ------------------------------- #

        dm_sent = True
        try:
            dm_embed = _base_embed(COLOR_WARN)
            dm_embed.title = "⚠️ You've received a warning"
            dm_embed.description = f"You were warned in **{interaction.guild.name}**."
            dm_embed.add_field(name="Reason", value=reason or "*No reason provided*", inline=False)
            dm_embed.add_field(name="Warning level", value=warning_bar(count), inline=False)
            dm_embed.add_field(
                name="Good to know",
                value=(
                    f"Warnings automatically expire **{WARNING_EXPIRY_DAYS} days** "
                    "after they're given if you don't receive another one."
                ),
                inline=False,
            )
            if interaction.guild.icon:
                dm_embed.set_thumbnail(url=interaction.guild.icon.url)
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            dm_sent = False

        # ================================================================ #

        #   BAN PATH (3rd+ warning)                                        #

        # ================================================================ #

        if count >= BAN_THRESHOLD:
            me = interaction.guild.me
            if not me or not me.guild_permissions.ban_members:
                embed = _base_embed(COLOR_DANGER)
                embed.title = "⚠️ Ban threshold reached — action blocked"
                embed.set_author(name=str(member), icon_url=member.display_avatar.url)
                embed.description = (
                    f"{member.mention} reached the warning threshold, but I'm missing the "
                    "**Ban Members** permission, so no action was taken automatically."
                )
                embed.add_field(name="Warning level", value=warning_bar(count), inline=False)
                return await interaction.followup.send(embed=embed)

            if member.top_role >= me.top_role:
                embed = _base_embed(COLOR_DANGER)
                embed.title = "⚠️ Ban threshold reached — action blocked"
                embed.set_author(name=str(member), icon_url=member.display_avatar.url)
                embed.description = (
                    f"{member.mention} reached the warning threshold, but their top role is "
                    "above or equal to mine, so no action was taken automatically."
                )
                embed.add_field(name="Warning level", value=warning_bar(count), inline=False)
                return await interaction.followup.send(embed=embed)

            try:
                await interaction.guild.ban(
                    member,
                    reason=f"AutoMod: reached {count} active warnings (by {interaction.user})",
                )
                log.info("AUTOMOD auto-ban: %s (3rd warning) by %s", member, interaction.user)

                embed = _base_embed(COLOR_DANGER)
                embed.title = "🔨 Member auto-banned"
                embed.set_author(name=str(member), icon_url=member.display_avatar.url)
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.description = (
                    f"{member.mention} reached the warning limit and has been **banned**."
                )
                embed.add_field(name="Warning level",  value=warning_bar(count),              inline=False)
                embed.add_field(name="Final reason",   value=reason or "*No reason provided*", inline=False)
                embed.add_field(name="Banned by",      value=interaction.user.mention,         inline=True)
                if not dm_sent:
                    embed.add_field(
                        name="Note",
                        value="⚠️ Couldn't DM the member before banning.",
                        inline=True,
                    )
                return await interaction.followup.send(embed=embed)

            except discord.Forbidden:
                embed = _base_embed(COLOR_DANGER)
                embed.title = "⚠️ Ban threshold reached, but ban failed"
                embed.set_author(name=str(member), icon_url=member.display_avatar.url)
                embed.description = (
                    f"{member.mention} reached the warning limit but Discord denied the ban."
                )
                return await interaction.followup.send(embed=embed)

        # ================================================================ #

        #   TIMEOUT PATH (1st and 2nd warning)                             #

        # ================================================================ #

        timeout_duration = TIMEOUT_DURATIONS.get(count)
        timeout_applied  = False
        timeout_note: str | None = None
        timeout_blocked: str | None = None  # human-readable reason if we couldn't time out


        if timeout_duration is not None:
            me = interaction.guild.me

            # Check bot permissions and hierarchy before attempting.

            if not me or not me.guild_permissions.moderate_members:
                timeout_blocked = "I'm missing the **Timeout Members** permission."
            elif member == interaction.guild.owner:
                # Already blocked above, but just in case.

                timeout_blocked = "Cannot timeout the server owner."
            else:
                timeout_applied, timeout_note = await _apply_timeout(
                    member,
                    timeout_duration,
                    reason=(
                        f"AutoMod: warning #{count} "
                        f"(by {interaction.user})"
                        + (f" — {reason}" if reason else "")
                    ),
                )
                if not timeout_applied and timeout_note:
                    timeout_blocked = timeout_note
                    timeout_note    = None

        # ---- Build the public confirmation embed ----------------------- #

        embed = _base_embed(COLOR_WARN)
        embed.title = "⚠️ Warning issued"
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(name="Member",    value=member.mention,           inline=True)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        # Spacer keeps the two-column row tidy.

        embed.add_field(name="\u200b",    value="\u200b",                 inline=True)

        embed.add_field(
            name="Reason",
            value=reason or "*No reason provided*",
            inline=False,
        )
        embed.add_field(name="Warning level", value=warning_bar(count), inline=False)

        # Timeout field.

        if timeout_applied and timeout_duration:
            embed.add_field(
                name="⏱️ Timeout applied",
                value=f"{member.mention} has been timed out for **{format_duration(timeout_duration)}**.",
                inline=False,
            )
        elif timeout_blocked:
            embed.add_field(
                name="⚠️ Timeout could not be applied",
                value=timeout_blocked,
                inline=False,
            )

        # Extra note about admin role manipulation.

        if timeout_note:
            embed.add_field(name="ℹ️ Additional info", value=timeout_note, inline=False)

        if not dm_sent:
            embed.add_field(
                name="Note",
                value="⚠️ Couldn't DM the member (DMs closed).",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    # ================================================================== #

    #   /warn list                                                        #

    # ================================================================== #


    @warn_group.command(name="list", description="Show a member's active warnings")
    @app_commands.describe(member="Member to check")
    async def warn_list(self, interaction: discord.Interaction, member: discord.Member):
        if not is_staff(interaction):
            return await interaction.response.send_message(
                embed=permission_denied_embed(), ephemeral=True
            )
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        active = await db.get_active_warnings(
            guild_id=interaction.guild.id, target_id=member.id
        )

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
            when   = f"<t:{_to_ts(w.get('created_at'))}:R>"
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

    # ================================================================== #

    #   /warn clear                                                       #

    # ================================================================== #


    @warn_group.command(name="clear", description="Clear all active warnings for a member")
    @app_commands.describe(member="Member to clear warnings for")
    async def warn_clear(self, interaction: discord.Interaction, member: discord.Member):
        if not is_staff(interaction):
            return await interaction.response.send_message(
                embed=permission_denied_embed(), ephemeral=True
            )
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        cleared = await db.clear_warnings(
            guild_id=interaction.guild.id,
            target_id=member.id,
            cleared_by=interaction.user.id,
        )

        if cleared == 0:
            embed = _base_embed(COLOR_SUCCESS)
            embed.title = "Nothing to clear"
            embed.set_author(name=str(member), icon_url=member.display_avatar.url)
            embed.description = f"{member.mention} had no active warnings."
        else:
            log.info(
                "AUTOMOD clear: %s cleared %s warning(s) for %s",
                interaction.user, cleared, member,
            )
            embed = _base_embed(COLOR_SUCCESS)
            embed.title = "✅ Warnings cleared"
            embed.set_author(name=str(member), icon_url=member.display_avatar.url)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.description = (
                f"Cleared **{cleared}** active "
                f"{'warning' if cleared == 1 else 'warnings'} "
                f"for {member.mention}."
            )
            embed.add_field(name="Cleared by", value=interaction.user.mention, inline=True)
            embed.add_field(name="New level",  value=warning_bar(0),           inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ================================================================== #

    #   Error handler                                                     #

    # ================================================================== #


    @warn_add.error
    @warn_list.error
    @warn_clear.error
    async def warn_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        log.exception("AutoMod command error", exc_info=error)
        embed = _base_embed(COLOR_DANGER)
        embed.description = "⚠️ **Something went wrong.**"
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoMod(bot))