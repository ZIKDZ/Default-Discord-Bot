import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core import supabase_client as db

log = logging.getLogger(__name__)

EXTRA_ALLOWED_ROLE_IDS = [1141838188650967200]

WARNING_EXPIRY_DAYS = db.WARNING_EXPIRY_DAYS
BAN_THRESHOLD       = 3
TIMEOUT_DURATIONS   = {1: timedelta(hours=1), 2: timedelta(days=1)}

COLOR_WARN    = 0xF2A93B
COLOR_DANGER  = 0xE0405A
COLOR_SUCCESS = 0x57C785
FOOTER_ICON   = "https://cdn-icons-png.flaticon.com/512/564/564619.png"
FOOTER_TEXT   = "AutoMod"


def fmt_duration(td: timedelta) -> str:
    s = int(td.total_seconds())
    if s % 86400 == 0:
        d = s // 86400
        return f"{d} day" if d == 1 else f"{d} days"
    h = s // 3600
    return f"{h} hour" if h == 1 else f"{h} hours"

def base_embed(color: int) -> discord.Embed:
    e = discord.Embed(color=color, timestamp=datetime.now(timezone.utc))
    e.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    return e

def warn_bar(n: int, t: int = BAN_THRESHOLD) -> str:
    return f"{'�' * min(n,t)}{'⬜' * max(t-n,0)}  `{n}/{t}`"

def is_staff(i: discord.Interaction) -> bool:
    if not i.guild: return False
    return i.user.guild_permissions.administrator or \
           any(r.id in EXTRA_ALLOWED_ROLE_IDS for r in i.user.roles)

def no_perms() -> discord.Embed:
    e = base_embed(COLOR_DANGER)
    e.description = "� **You don't have permission.**"
    return e

def _to_ts(iso: str | None) -> int:
    try: return int(datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp())
    except: return int(datetime.now(timezone.utc).timestamp())


# ── punishment state: member_id -> stripped admin roles ──────────────────────
_stripped: dict[int, list[discord.Role]] = {}


async def _unpunish(guild: discord.Guild, member: discord.Member, *, reason: str) -> None:
    """Lift timeout and restore any stripped admin roles."""
    try:
        await member.timeout(None, reason=reason)
    except discord.HTTPException:
        pass

    roles = _stripped.pop(member.id, [])
    if roles:
        alive = [r for r in roles if r in guild.roles]
        if alive:
            try:
                await member.add_roles(*alive, reason=reason, atomic=False)
            except discord.HTTPException as e:
                log.warning("role-restore failed for %s: %s", member, e)


async def _punish(guild: discord.Guild, member: discord.Member,
                  duration: timedelta, reason: str) -> str | None:
    """
    Strip admin roles → apply timeout → schedule auto-unpunish.
    Returns a note string if admin roles were stripped, else None.
    Returns a string starting with ❌ on failure.
    """
    admin_roles = [r for r in member.roles if r.id != guild.id and r.permissions.administrator]

    if admin_roles:
        try:
            await member.remove_roles(*admin_roles, reason="AutoMod: timeout prep", atomic=False)
            _stripped[member.id] = admin_roles
        except discord.HTTPException as e:
            return f"❌ Could not remove admin role(s): {e}"

    try:
        await member.timeout(datetime.now(timezone.utc) + duration, reason=reason)
    except discord.HTTPException as e:
        if admin_roles:
            try: await member.add_roles(*admin_roles, atomic=False)
            except: pass
            _stripped.pop(member.id, None)
        return f"❌ Timeout failed: {e}"

    async def _auto_restore():
        await asyncio.sleep(duration.total_seconds())
        try:
            m = await guild.fetch_member(member.id)
            await _unpunish(guild, m, reason="AutoMod: timeout expired")
        except discord.NotFound:
            _stripped.pop(member.id, None)
        except discord.HTTPException as e:
            log.warning("auto-restore failed for %s: %s", member.id, e)

    asyncio.get_event_loop().create_task(_auto_restore(), name=f"restore-{member.id}")

    if admin_roles:
        names = ", ".join(f"`{r.name}`" for r in admin_roles)
        return (f"⚠️ Admin role(s) {names} removed for the timeout duration "
                f"(**{fmt_duration(duration)}**) and will be restored automatically.")
    return None


# ─────────────────────────────────────────────────────────────────────────────

class AutoMod(commands.Cog):
    def __init__(self, bot): self.bot = bot

    # ── /warn ────────────────────────────────────────────────────────────────

    @app_commands.command(name="warn", description="Warn a member. 3 active warnings = auto-ban.")
    @app_commands.describe(member="Member to warn", reason="Reason")
    async def warn(self, i: discord.Interaction, member: discord.Member, reason: str | None = None):
        if not is_staff(i):
            return await i.response.send_message(embed=no_perms(), ephemeral=True)
        if not i.guild or member.bot or member == i.guild.owner:
            e = base_embed(COLOR_DANGER)
            e.description = "� Invalid target."
            return await i.response.send_message(embed=e, ephemeral=True)

        await i.response.defer()

        if not await db.add_warning(
            guild_id=i.guild.id,
            target_id=member.id,
            moderator_id=i.user.id,
            reason=reason,
        ):
            e = base_embed(COLOR_DANGER)
            e.title = "DB error"
            e.description = "Warning not saved."
            return await i.followup.send(embed=e)

        active = await db.get_active_warnings(guild_id=i.guild.id, target_id=member.id)
        count  = len(active)

        # DM
        dm_ok = True
        try:
            dm = base_embed(COLOR_WARN)
            dm.title = "⚠️ You received a warning"
            dm.description = f"Warned in **{i.guild.name}**."
            dm.add_field(name="Reason",        value=reason or "*None*", inline=False)
            dm.add_field(name="Warning level", value=warn_bar(count),    inline=False)
            if i.guild.icon: dm.set_thumbnail(url=i.guild.icon.url)
            await member.send(embed=dm)
        except discord.Forbidden:
            dm_ok = False

        # ── ban path ─────────────────────────────────────────────────────────
        if count >= BAN_THRESHOLD:
            me = i.guild.me
            blocked = (
                "Missing **Ban Members** permission." if not me.guild_permissions.ban_members else
                "My role is too low."                 if member.top_role >= me.top_role       else None
            )
            e = base_embed(COLOR_DANGER)
            e.set_author(name=str(member), icon_url=member.display_avatar.url)
            if blocked:
                e.title = "⚠️ Ban threshold reached — blocked"
                e.description = blocked
                e.add_field(name="Warning level", value=warn_bar(count), inline=False)
                return await i.followup.send(embed=e)
            try:
                await i.guild.ban(member, reason=f"AutoMod: {count} warnings by {i.user}")
                e.title = "� Auto-banned"
                e.description = f"{member.mention} reached the warning limit."
                e.add_field(name="Warning level", value=warn_bar(count),    inline=False)
                e.add_field(name="Reason",        value=reason or "*None*", inline=False)
                e.add_field(name="By",            value=i.user.mention,     inline=True)
                if not dm_ok: e.add_field(name="Note", value="Couldn't DM member.", inline=True)
            except discord.Forbidden:
                e.title = "⚠️ Ban failed"
                e.description = "Discord denied the ban."
            return await i.followup.send(embed=e)

        # ── timeout path ─────────────────────────────────────────────────────
        td      = TIMEOUT_DURATIONS.get(count)
        note    = None
        blocked = None

        if td:
            me = i.guild.me
            if not me.guild_permissions.moderate_members:
                blocked = "Missing **Timeout Members** permission."
            else:
                result = await _punish(
                    i.guild, member, td,
                    f"AutoMod: warning #{count} by {i.user}" + (f" — {reason}" if reason else ""),
                )
                if result and result.startswith("❌"):
                    blocked = result
                else:
                    note = result

        e = base_embed(COLOR_WARN)
        e.title = "⚠️ Warning issued"
        e.set_author(name=str(member), icon_url=member.display_avatar.url)
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="Member",        value=member.mention,    inline=True)
        e.add_field(name="Moderator",     value=i.user.mention,    inline=True)
        e.add_field(name="\u200b",        value="\u200b",           inline=True)
        e.add_field(name="Reason",        value=reason or "*None*", inline=False)
        e.add_field(name="Warning level", value=warn_bar(count),    inline=False)
        if td and not blocked:
            e.add_field(name="⏱️ Timeout", value=f"Timed out for **{fmt_duration(td)}**.", inline=False)
        if blocked: e.add_field(name="⚠️ Timeout failed", value=blocked, inline=False)
        if note:    e.add_field(name="ℹ️ Info",            value=note,    inline=False)
        if not dm_ok: e.add_field(name="Note", value="Couldn't DM member.", inline=False)
        await i.followup.send(embed=e)

    # ── /warnings ────────────────────────────────────────────────────────────

    @app_commands.command(name="warnings", description="Show a member's active warnings.")
    @app_commands.describe(member="Member to check")
    async def warnings(self, i: discord.Interaction, member: discord.Member):
        if not is_staff(i): return await i.response.send_message(embed=no_perms(), ephemeral=True)
        await i.response.defer(ephemeral=True)

        active = await db.get_active_warnings(guild_id=i.guild.id, target_id=member.id)
        if not active:
            e = base_embed(COLOR_SUCCESS)
            e.title = "✅ Clean record"
            e.description = f"{member.mention} has no active warnings."
            return await i.followup.send(embed=e, ephemeral=True)

        e = base_embed(COLOR_WARN)
        e.title = f"� Warnings — {len(active)}"
        e.set_author(name=str(member), icon_url=member.display_avatar.url)
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="Warning level", value=warn_bar(len(active)), inline=False)
        for idx, w in enumerate(active, 1):
            e.add_field(name=f"#{idx}", inline=False,
                        value=f"{w.get('reason') or '*None*'}\n*<t:{_to_ts(w.get('created_at'))}:R>*")
        await i.followup.send(embed=e, ephemeral=True)

    # ── /clearwarnings ───────────────────────────────────────────────────────

    @app_commands.command(name="clearwarnings", description="Clear warnings, lift timeout and restore roles.")
    @app_commands.describe(member="Member to clear")
    async def clearwarnings(self, i: discord.Interaction, member: discord.Member):
        if not is_staff(i): return await i.response.send_message(embed=no_perms(), ephemeral=True)
        await i.response.defer(ephemeral=True)

        cleared = await db.clear_warnings(
            guild_id=i.guild.id,
            target_id=member.id,
            cleared_by=i.user.id,
        )
        await _unpunish(i.guild, member, reason=f"AutoMod: warnings cleared by {i.user}")

        e = base_embed(COLOR_SUCCESS)
        e.set_author(name=str(member), icon_url=member.display_avatar.url)
        e.set_thumbnail(url=member.display_avatar.url)
        if cleared == 0:
            e.title = "Nothing to clear"
            e.description = f"{member.mention} had no active warnings."
        else:
            e.title = "✅ Cleared"
            e.description = (f"Cleared **{cleared}** warning(s) for {member.mention}.\n"
                             "Timeout lifted and roles restored.")
            e.add_field(name="By",        value=i.user.mention, inline=True)
            e.add_field(name="New level", value=warn_bar(0),    inline=True)
        await i.followup.send(embed=e, ephemeral=True)

    # ── errors ───────────────────────────────────────────────────────────────

    @warn.error
    @warnings.error
    @clearwarnings.error
    async def on_error(self, i: discord.Interaction, error: app_commands.AppCommandError):
        log.exception("AutoMod error", exc_info=error)
        e = base_embed(COLOR_DANGER)
        e.description = "⚠️ Something went wrong."
        if i.response.is_done(): await i.followup.send(embed=e, ephemeral=True)
        else: await i.response.send_message(embed=e, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoMod(bot))