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

# ==========================================================
# CUSTOM ICONS (Discord "Application Emojis")
#
# How to set these up:
#   1. Go to the Discord Developer Portal -> your application -> "Emojis" tab.
#   2. Upload one image per row below (any name is fine, keep it recognizable).
#   3. After uploading, hover the emoji in the portal and copy its ID
#      (or send it once in any server the bot can post custom emoji info in
#      and copy the ID from the raw `<:name:id>` form).
#   4. Paste the numeric ID as the value for that key below. Leave any entry
#      as None to keep using the default unicode emoji for that item instead
#      — nothing breaks if you only upload some of them.
#
# Application emojis work in any server the bot is in and do NOT require the
# server to have USE_EXTERNAL_EMOJIS granted to the bot.
# ==========================================================
ICON_EMOJI_IDS: dict[str, int | None] = {
    # Channel settings
    "name":     1530360626885689476,   # edit
    "limit":    1530360624788406312,   # group
    "status":   1530360622733328464,   # chat
    "game":     1530360621407670404,   # games
    "bitrate":  1530360619927208067,   # graph
    "region":   1530360610439561497,   # world
    "claim":    1530360608841535669,   # crown
    "transfer": 1530361618611962027,                  # 🔄  (no matching emoji uploaded)
    "load":     1530361840817930262,                  # 📥  (no matching emoji uploaded)
    # Channel permissions
    "lock":     1530360595600379954,   # lock
    "unlock":   1530360607336042516,   # openpadlock
    "permit":   1530360593410691216,   # adduser
    "reject":   1530360592387276981,   # removeuser
    "invite":   1530363098362417153,                  # 📨  (no matching emoji uploaded)
    "ghost":    1530362688381784064,                  # 👻  (no matching emoji uploaded)
    "unghost":  1530362691032711290,                  # 👁️  (no matching emoji uploaded)
}

# Fallback unicode emoji, used per-key whenever ICON_EMOJI_IDS[key] is None.
_UNICODE_FALLBACK = {
    "name": "✏️", "limit": "👥", "status": "💬", "game": "🎮", "bitrate": "🎚️",
    "region": "🌐", "claim": "👑", "transfer": "🔄", "load": "📥",
    "lock": "🔒", "unlock": "🔓", "permit": "✅", "reject": "⛔",
    "invite": "📨", "ghost": "👻", "unghost": "👁️",
}

# Panel embed author icon — set this to one uploaded application emoji's ID to
# brand the panel itself. Leave None to use BRAND_ICON below instead.
PANEL_ICON_EMOJI_ID: int | None = None


def icon(key: str) -> str:
    """Returns the emoji string for a dropdown option: a custom application
    emoji (<:name:id> form) if configured, otherwise the unicode fallback."""
    emoji_id = ICON_EMOJI_IDS.get(key)
    if emoji_id:
        return f"<:{key}:{emoji_id}>"
    return _UNICODE_FALLBACK.get(key, "")


def icon_url(emoji_id: int) -> str:
    """CDN URL for an application/custom emoji, usable as an embed thumbnail
    or author icon."""
    return f"https://cdn.discordapp.com/emojis/{emoji_id}.png"


BOT_COLOR = 0x5865F2
SUCCESS_COLOR = 0x57C785
DANGER_COLOR = 0xE0405A
BRAND_ICON = "https://cdn-icons-png.flaticon.com/512/727/727269.png"  # swap for your bot's icon

DEFAULT_NAME_TEMPLATE = "{user}'s Channel"

# In-memory cache of hub configs so we don't hit Supabase on every voice
# state update. Refreshed on write and lazily on miss.
_hub_cache: dict[int, dict | None] = {}


def is_staff(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    perms = interaction.user.guild_permissions
    role_ids = [r.id for r in interaction.user.roles]
    return perms.administrator or any(rid in EXTRA_ALLOWED_ROLE_IDS for rid in role_ids)


def base_embed(color: int = BOT_COLOR) -> discord.Embed:
    e = discord.Embed(color=color, timestamp=datetime.now(timezone.utc))
    return e


def no_perms_embed() -> discord.Embed:
    e = base_embed(DANGER_COLOR)
    e.description = "🚫 **You don't have permission to do that.**"
    return e


def not_your_channel_embed() -> discord.Embed:
    e = base_embed(DANGER_COLOR)
    e.description = "🚫 **You don't own a temp voice channel right now.**\nJoin the hub channel to create one."
    return e


def render_channel_name(template: str, member: discord.Member) -> str:
    try:
        name = template.format(user=member.display_name, username=member.name)
    except Exception:
        name = f"{member.display_name}'s Channel"
    return name[:100] or f"{member.display_name}'s Channel"


# ================================================================
#   Ownership / state helpers
# ================================================================

async def get_owned_channel(guild: discord.Guild, user_id: int) -> discord.VoiceChannel | None:
    """Finds the temp voice channel the given user currently owns, if any."""
    rows = await db.get_all_temp_channels(guild_id=guild.id)
    for row in rows:
        if int(row["owner_id"]) == user_id:
            ch = guild.get_channel(int(row["channel_id"]))
            if isinstance(ch, discord.VoiceChannel):
                return ch
    return None


async def resolve_channel_and_row(
    interaction: discord.Interaction,
) -> tuple[discord.VoiceChannel | None, dict | None]:
    """
    Resolves the temp channel relevant to this interaction:
    the user's own voice channel if they're in one AND it's a tracked temp
    channel they own; otherwise looks up any temp channel they own elsewhere.
    """
    guild = interaction.guild
    if not guild:
        return None, None

    member = interaction.user
    voice_state = member.voice
    if voice_state and voice_state.channel:
        row = await db.get_temp_channel(channel_id=voice_state.channel.id)
        if row and int(row["owner_id"]) == member.id:
            return voice_state.channel, row

    owned = await get_owned_channel(guild, member.id)
    if owned:
        row = await db.get_temp_channel(channel_id=owned.id)
        return owned, row

    return None, None


async def sync_channel_state(channel: discord.VoiceChannel, row: dict) -> None:
    """Re-applies locked/ghosted overwrite state to @everyone. Called after
    permission-affecting actions to keep the channel consistent."""
    everyone = channel.guild.default_role
    overwrite = channel.overwrites_for(everyone)
    overwrite.connect = False if row.get("locked") else None
    overwrite.view_channel = False if row.get("ghosted") else None
    await channel.set_permissions(everyone, overwrite=overwrite, reason="VoiceMaster: state sync")


# ================================================================
#   Panel embed + view
# ================================================================

def build_panel_embed(channel: discord.VoiceChannel, owner: discord.abc.User) -> discord.Embed:
    author_icon = icon_url(PANEL_ICON_EMOJI_ID) if PANEL_ICON_EMOJI_ID else BRAND_ICON

    e = base_embed(BOT_COLOR)
    e.set_author(name="Welcome to your own temporary voice channel", icon_url=author_icon)
    e.description = (
        "Control your channel using the menus below\n"
        "• Use the dropdowns to manage settings and permissions\n\n"
        f"**Owner:** {owner.mention}\n"
        f"**Channel:** {channel.mention}"
    )
    if PANEL_ICON_EMOJI_ID:
        e.set_thumbnail(url=icon_url(PANEL_ICON_EMOJI_ID))
    e.set_footer(text="VoiceMaster • Panel refreshes each time someone joins")
    return e


class ChannelSettingsSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Name", description="Change the channel name", emoji=icon("name"), value="name"),
            discord.SelectOption(label="Limit", description="Change the channel limit", emoji=icon("limit"), value="limit"),
            discord.SelectOption(label="Status", description="Set a custom voice channel status", emoji=icon("status"), value="status"),
            discord.SelectOption(label="Game", description="Change the channel name to the game you're playing", emoji=icon("game"), value="game"),
            discord.SelectOption(label="Bitrate", description="Change the channel bitrate", emoji=icon("bitrate"), value="bitrate"),
            discord.SelectOption(label="Region", description="Change the channel voice region", emoji=icon("region"), value="region"),
            discord.SelectOption(label="Claim", description="Claim ownership of the channel", emoji=icon("claim"), value="claim"),
            discord.SelectOption(label="Transfer", description="Transfer ownership to another user", emoji=icon("transfer"), value="transfer"),
            discord.SelectOption(label="Load Settings", description="Apply your saved profile to this channel", emoji=icon("load"), value="load"),
        ]
        super().__init__(placeholder="Change channel settings", options=options, min_values=1, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction):
        cog: "VoiceMaster" = interaction.client.get_cog("VoiceMaster")  # type: ignore
        await cog.handle_settings_action(interaction, self.values[0])


class ChannelPermissionsSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Lock", description="Lock the channel", emoji=icon("lock"), value="lock"),
            discord.SelectOption(label="Unlock", description="Unlock the channel", emoji=icon("unlock"), value="unlock"),
            discord.SelectOption(label="Permit", description="Permit a user/role to access the channel", emoji=icon("permit"), value="permit"),
            discord.SelectOption(label="Reject", description="Reject/kick a user/role from the channel", emoji=icon("reject"), value="reject"),
            discord.SelectOption(label="Invite", description="Invite a user to access the channel", emoji=icon("invite"), value="invite"),
            discord.SelectOption(label="Ghost", description="Make your channel invisible", emoji=icon("ghost"), value="ghost"),
            discord.SelectOption(label="Unghost", description="Make your channel visible", emoji=icon("unghost"), value="unghost"),
        ]
        super().__init__(placeholder="Change channel permissions", options=options, min_values=1, max_values=1, row=1)

    async def callback(self, interaction: discord.Interaction):
        cog: "VoiceMaster" = interaction.client.get_cog("VoiceMaster")  # type: ignore
        await cog.handle_permissions_action(interaction, self.values[0])


class VoicePanelView(discord.ui.View):
    """Fresh, non-persistent view — a new one is posted on every join, per spec."""

    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(ChannelSettingsSelect())
        self.add_item(ChannelPermissionsSelect())

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ================================================================
#   Modals
# ================================================================

class RenameModal(discord.ui.Modal, title="Rename Channel"):
    name = discord.ui.TextInput(label="New channel name", max_length=100, required=True)

    def __init__(self, cog: "VoiceMaster", channel: discord.VoiceChannel):
        super().__init__()
        self.cog = cog
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.do_rename(interaction, self.channel, str(self.name.value))


class LimitModal(discord.ui.Modal, title="Set User Limit"):
    limit = discord.ui.TextInput(
        label="Limit (0 = unlimited, max 99)", max_length=2, required=True, placeholder="0"
    )

    def __init__(self, cog: "VoiceMaster", channel: discord.VoiceChannel):
        super().__init__()
        self.cog = cog
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.limit.value).strip()
        if not raw.isdigit() or not (0 <= int(raw) <= 99):
            error_embed = discord.Embed(description="🚫 Enter a number between 0 and 99.", color=DANGER_COLOR)
            return await interaction.response.send_message(embed=error_embed, ephemeral=True)
        await self.cog.do_limit(interaction, self.channel, int(raw))


class UserOrRoleModal(discord.ui.Modal):
    target = discord.ui.TextInput(
        label="User or role", placeholder="@mention, ID, or exact name", max_length=100, required=True
    )

    def __init__(self, cog: "VoiceMaster", channel: discord.VoiceChannel, action: str, title: str):
        super().__init__(title=title)
        self.cog = cog
        self.channel = channel
        self.action = action

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.do_permit_reject(interaction, self.channel, self.action, str(self.target.value))


class InviteModal(discord.ui.Modal, title="Invite User"):
    target = discord.ui.TextInput(
        label="User", placeholder="@mention, ID, or exact name", max_length=100, required=True
    )

    def __init__(self, cog: "VoiceMaster", channel: discord.VoiceChannel):
        super().__init__()
        self.cog = cog
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        target = VoiceMaster.resolve_member_or_role(interaction.guild, str(self.target.value))
        if not isinstance(target, discord.Member):
            return await interaction.response.send_message(
                f"🚫 Couldn't find a member matching `{self.target.value}`.", ephemeral=True
            )
        await self.cog.do_invite(interaction, self.channel, target)


class TransferModal(discord.ui.Modal, title="Transfer Ownership"):
    target = discord.ui.TextInput(label="New owner (@mention, ID, or name)", max_length=100, required=True)

    def __init__(self, cog: "VoiceMaster", channel: discord.VoiceChannel):
        super().__init__()
        self.cog = cog
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.do_transfer(interaction, self.channel, str(self.target.value))


class StatusModal(discord.ui.Modal, title="Set Voice Status"):
    status = discord.ui.TextInput(
        label="Status",
        placeholder="e.g. 🎮 Playing ranked  (leave blank to clear)",
        max_length=500,
        required=False,
    )

    def __init__(self, cog: "VoiceMaster", channel: discord.VoiceChannel):
        super().__init__()
        self.cog = cog
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.do_status(interaction, self.channel, str(self.status.value))


class GameModal(discord.ui.Modal, title="Set Channel Name to Game"):
    game = discord.ui.TextInput(label="Game name", max_length=90, required=True, placeholder="e.g. Valorant")

    def __init__(self, cog: "VoiceMaster", channel: discord.VoiceChannel):
        super().__init__()
        self.cog = cog
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.do_rename(interaction, self.channel, f"🎮 {str(self.game.value).strip()}")


class BitrateModal(discord.ui.Modal, title="Set Bitrate"):
    bitrate = discord.ui.TextInput(
        label="Bitrate in kbps (8-96, up to 384 boosted)", max_length=3, required=True, placeholder="64"
    )

    def __init__(self, cog: "VoiceMaster", channel: discord.VoiceChannel):
        super().__init__()
        self.cog = cog
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.bitrate.value).strip()
        if not raw.isdigit():
            error_embed = discord.Embed(description="🚫 Enter a whole number of kbps.", color=DANGER_COLOR)
            return await interaction.response.send_message(embed=error_embed, ephemeral=True)
        await self.cog.do_bitrate(interaction, self.channel, int(raw))


# Discord's standard fixed voice region set (id, display label)
VOICE_REGIONS = [
    ("automatic", "Automatic"),
    ("us-west", "US West"),
    ("us-east", "US East"),
    ("us-central", "US Central"),
    ("us-south", "US South"),
    ("brazil", "Brazil"),
    ("hongkong", "Hong Kong"),
    ("india", "India"),
    ("japan", "Japan"),
    ("rotterdam", "Rotterdam"),
    ("russia", "Russia"),
    ("singapore", "Singapore"),
    ("southafrica", "South Africa"),
    ("sydney", "Sydney"),
    ("south-korea", "South Korea"),
]


class RegionSelect(discord.ui.Select):
    def __init__(self, cog: "VoiceMaster", channel: discord.VoiceChannel):
        self.cog = cog
        self.channel = channel
        options = [
            discord.SelectOption(label=label, value=region_id)
            for region_id, label in VOICE_REGIONS
        ]
        super().__init__(placeholder="Choose a voice region", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        await self.cog.do_region(interaction, self.channel, self.values[0])


class RegionSelectView(discord.ui.View):
    def __init__(self, cog: "VoiceMaster", channel: discord.VoiceChannel):
        super().__init__(timeout=120)
        self.add_item(RegionSelect(cog, channel))


# ================================================================
#   Cog
# ================================================================

class VoiceMaster(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------------------------------------------------------
    #   Hub config resolution (cached)
    # ---------------------------------------------------------

    async def get_hub(self, guild_id: int) -> dict | None:
        if guild_id in _hub_cache:
            return _hub_cache[guild_id]
        hub = await db.get_hub(guild_id=guild_id)
        _hub_cache[guild_id] = hub
        return hub

    def invalidate_hub(self, guild_id: int) -> None:
        _hub_cache.pop(guild_id, None)

    # ---------------------------------------------------------
    #   Resolving a user/role from free text (for Permit/Reject/Transfer)
    # ---------------------------------------------------------

    @staticmethod
    def resolve_member_or_role(guild: discord.Guild, raw: str):
        raw = raw.strip()
        if raw.startswith("<@") and raw.endswith(">"):
            digits = "".join(c for c in raw if c.isdigit())
            if digits:
                return guild.get_member(int(digits)) or guild.get_role(int(digits))
        if raw.startswith("<@&") and raw.endswith(">"):
            digits = "".join(c for c in raw if c.isdigit())
            if digits:
                return guild.get_role(int(digits))
        if raw.isdigit():
            return guild.get_member(int(raw)) or guild.get_role(int(raw))
        lowered = raw.lower()
        for m in guild.members:
            if m.name.lower() == lowered or m.display_name.lower() == lowered:
                return m
        for r in guild.roles:
            if r.name.lower() == lowered:
                return r
        return None

    # ---------------------------------------------------------
    #   /voice setup
    # ---------------------------------------------------------

    voice_group = app_commands.Group(name="voice", description="VoiceMaster temp voice channel controls")

    @voice_group.command(name="setup", description="(Staff) Create/reconfigure the Join-to-Create voice hub")
    @app_commands.describe(
        category="Category new temp channels should spawn in (defaults to the hub's own category)",
        panel_channel="Text channel to post control panels in (defaults to the temp VC's own text chat)",
        name_template="Template for temp channel names. Use {user} for their display name.",
        default_limit="Default user limit for new temp channels (0 = unlimited)",
    )
    async def setup_cmd(
        self,
        interaction: discord.Interaction,
        category: discord.CategoryChannel | None = None,
        panel_channel: discord.TextChannel | None = None,
        name_template: str | None = None,
        default_limit: app_commands.Range[int, 0, 99] = 0,
    ):
        if not is_staff(interaction):
            return await interaction.response.send_message(embed=no_perms_embed(), ephemeral=True)
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        guild = interaction.guild
        me = guild.me
        if not me or not me.guild_permissions.manage_channels or not me.guild_permissions.move_members:
            return await interaction.response.send_message(
                "⚠️ I need **Manage Channels** and **Move Members** permissions to run VoiceMaster.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        target_category = category
        try:
            join_channel = await guild.create_voice_channel(
                name="➕ Join to Create",
                category=target_category,
                reason=f"VoiceMaster hub setup by {interaction.user}",
            )
        except discord.Forbidden:
            return await interaction.followup.send("⚠️ I don't have permission to create channels here.", ephemeral=True)

        ok = await db.set_hub(
            guild_id=guild.id,
            join_channel_id=join_channel.id,
            category_id=(target_category.id if target_category else join_channel.category_id),
            panel_channel_id=(panel_channel.id if panel_channel else None),
            name_template=name_template or DEFAULT_NAME_TEMPLATE,
            default_limit=default_limit,
            created_by=interaction.user.id,
        )
        self.invalidate_hub(guild.id)

        if not ok:
            e = base_embed(DANGER_COLOR)
            e.description = (
                "⚠️ Channel was created, but I couldn't save the config to the database. "
                "VoiceMaster won't work until this is fixed — check Supabase connectivity."
            )
            return await interaction.followup.send(embed=e, ephemeral=True)

        category_desc = target_category.mention if target_category else "same as hub"
        panel_desc = panel_channel.mention if panel_channel else "each temp channel's own text chat"

        e = base_embed(SUCCESS_COLOR)
        e.title = "✅ VoiceMaster hub ready"
        e.description = (
            f"Users who join {join_channel.mention} will get their own temp voice channel.\n\n"
            f"**Category:** {category_desc}\n"
            f"**Panel channel:** {panel_desc}\n"
            f"**Name template:** `{name_template or DEFAULT_NAME_TEMPLATE}`\n"
            f"**Default limit:** {default_limit or 'unlimited'}"
        )
        await interaction.followup.send(embed=e, ephemeral=True)

    @setup_cmd.error
    async def setup_cmd_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        log.exception("VoiceMaster setup error", exc_info=error)
        msg = "⚠️ Something went wrong."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    # ---------------------------------------------------------
    #   Voice state listener: the actual join-to-create engine
    # ---------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        if member.bot:
            return
        guild = member.guild

        # Joined a channel (including channel switches)
        if after.channel and after.channel != before.channel:
            hub = await self.get_hub(guild.id)
            if hub and after.channel.id == int(hub["join_channel_id"]):
                await self._create_temp_channel(member, hub)
            else:
                # Joining a tracked temp channel updates join order (for transfer target)
                row = await db.get_temp_channel(channel_id=after.channel.id)
                if row and int(row["owner_id"]) != member.id:
                    order = row.get("join_order") or []
                    if member.id not in order:
                        order.append(member.id)
                        await db.update_temp_channel(channel_id=after.channel.id, join_order=order)

        # Left a channel (including channel switches)
        if before.channel and before.channel != after.channel:
            row = await db.get_temp_channel(channel_id=before.channel.id)
            if row:
                await self._handle_leave(guild, before.channel, row, member)

    async def _create_temp_channel(self, member: discord.Member, hub: dict) -> None:
        guild = member.guild
        category_id = hub.get("category_id")
        category = guild.get_channel(int(category_id)) if category_id else None

        name = render_channel_name(hub.get("name_template") or DEFAULT_NAME_TEMPLATE, member)
        limit = hub.get("default_limit") or 0

        try:
            channel = await guild.create_voice_channel(
                name=name,
                category=category if isinstance(category, discord.CategoryChannel) else None,
                user_limit=limit,
                reason=f"VoiceMaster: temp channel for {member}",
            )
            # Owner always gets explicit management access to their own channel
            await channel.set_permissions(
                member,
                manage_channels=True,
                move_members=True,
                reason="VoiceMaster: owner permissions",
            )
            await member.move_to(channel, reason="VoiceMaster: join to create")
        except discord.Forbidden:
            log.warning("VoiceMaster: missing permissions to create/move into temp channel in guild %s", guild.id)
            return
        except discord.HTTPException:
            log.exception("VoiceMaster: failed to create temp channel")
            return

        await db.create_temp_channel(guild_id=guild.id, channel_id=channel.id, owner_id=member.id)

        panel_channel_id = hub.get("panel_channel_id")
        panel_channel = guild.get_channel(int(panel_channel_id)) if panel_channel_id else None
        if not isinstance(panel_channel, discord.TextChannel):
            # Fall back to the voice channel's own built-in text chat
            panel_channel = channel

        embed = build_panel_embed(channel, member)
        view = VoicePanelView()

        me = guild.me
        perms = panel_channel.permissions_for(me) if me else None
        can_send = bool(perms and perms.send_messages and perms.embed_links)

        if not can_send:
            log.warning(
                "VoiceMaster: missing send_messages/embed_links in panel channel %s (guild %s) — "
                "falling back to DM",
                getattr(panel_channel, "id", None),
                guild.id,
            )
            await self._send_panel_fallback(member, channel, embed, view, reason="missing_permissions")
            return

        try:
            msg = await panel_channel.send(embed=embed, view=view)
            await db.update_temp_channel(
                channel_id=channel.id, panel_msg_id=msg.id, panel_chan_id=panel_channel.id
            )
        except discord.HTTPException:
            log.exception("VoiceMaster: failed to post control panel in %s", getattr(panel_channel, "id", None))
            await self._send_panel_fallback(member, channel, embed, view, reason="send_failed")

    async def _send_panel_fallback(
        self,
        member: discord.Member,
        channel: discord.VoiceChannel,
        embed: discord.Embed,
        view: "VoicePanelView",
        *,
        reason: str,
    ) -> None:
        """
        Last-resort delivery when the panel can't be posted in the temp channel's
        text chat (usually: the bot lacks Send Messages/Embed Links there, which
        is common because voice-channel text chat inherits category permissions
        rather than getting any default grant). DMs the owner so they still get
        working controls instead of silently getting nothing.
        """
        try:
            await member.send(
                content=(
                    f"⚠️ I couldn't post your VoiceMaster control panel in {channel.mention} "
                    "(I may be missing **Send Messages** or **Embed Links** there). "
                    "Here are your controls instead:"
                ),
                embed=embed,
                view=view,
            )
        except discord.HTTPException:
            log.warning(
                "VoiceMaster: panel fallback DM also failed for %s in channel %s (reason=%s)",
                member.id, channel.id, reason,
            )

    async def _handle_leave(
        self, guild: discord.Guild, channel: discord.VoiceChannel, row: dict, member: discord.Member
    ) -> None:
        # Refresh channel members after the state update has settled
        remaining = [m for m in channel.members if not m.bot]

        if not remaining:
            try:
                await channel.delete(reason="VoiceMaster: temp channel empty")
            except discord.HTTPException:
                log.exception("VoiceMaster: failed to delete empty temp channel")
            await db.delete_temp_channel(channel_id=channel.id)
            return

        order = [uid for uid in (row.get("join_order") or []) if uid != member.id]
        if member.id in order:
            order.remove(member.id)

        if int(row["owner_id"]) == member.id:
            # Owner left -> transfer to the second member that joined the room,
            # i.e. the oldest entry in join_order still present in the channel.
            new_owner = None
            remaining_ids = {m.id for m in remaining}
            for uid in order:
                if uid in remaining_ids:
                    new_owner = guild.get_member(uid)
                    break
            if not new_owner:
                new_owner = remaining[0]

            try:
                await channel.set_permissions(
                    guild.get_member(member.id) or member,
                    overwrite=None,
                    reason="VoiceMaster: ownership transferred away",
                )
                await channel.set_permissions(
                    new_owner, manage_channels=True, move_members=True, reason="VoiceMaster: new owner"
                )
            except discord.HTTPException:
                log.exception("VoiceMaster: failed to update permissions on transfer")

            order = [uid for uid in order if uid != new_owner.id]
            await db.update_temp_channel(channel_id=channel.id, owner_id=new_owner.id, join_order=order)

            try:
                e = base_embed(BOT_COLOR)
                e.description = f"👑 {new_owner.mention} is now the owner of this channel (previous owner left)."
                await channel.send(embed=e)
            except discord.HTTPException:
                pass
        else:
            await db.update_temp_channel(channel_id=channel.id, join_order=order)

    # ---------------------------------------------------------
    #   Ownership check used by every action
    # ---------------------------------------------------------

    async def _require_ownership(
        self, interaction: discord.Interaction
    ) -> tuple[discord.VoiceChannel, dict] | tuple[None, None]:
        channel, row = await resolve_channel_and_row(interaction)
        if not channel or not row:
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=not_your_channel_embed(), ephemeral=True)
            else:
                await interaction.followup.send(embed=not_your_channel_embed(), ephemeral=True)
            return None, None
        return channel, row

    # ---------------------------------------------------------
    #   Dropdown routing
    # ---------------------------------------------------------

    async def handle_settings_action(self, interaction: discord.Interaction, action: str):
        if action == "claim":
            return await self.do_claim(interaction)
        if action == "load":
            return await self.do_load_settings(interaction)

        channel, row = await self._require_ownership(interaction)
        if not channel:
            return

        if action == "name":
            return await interaction.response.send_modal(RenameModal(self, channel))
        if action == "limit":
            return await interaction.response.send_modal(LimitModal(self, channel))
        if action == "transfer":
            return await interaction.response.send_modal(TransferModal(self, channel))
        if action == "status":
            return await interaction.response.send_modal(StatusModal(self, channel))
        if action == "game":
            return await interaction.response.send_modal(GameModal(self, channel))
        if action == "bitrate":
            return await interaction.response.send_modal(BitrateModal(self, channel))
        if action == "region":
            return await interaction.response.send_message(
                "🌐 Pick a voice region:", view=RegionSelectView(self, channel), ephemeral=True
            )

    async def handle_permissions_action(self, interaction: discord.Interaction, action: str):
        channel, row = await self._require_ownership(interaction)
        if not channel:
            return

        if action == "lock":
            return await self.do_lock(interaction, channel, row, locked=True)
        if action == "unlock":
            return await self.do_lock(interaction, channel, row, locked=False)
        if action == "ghost":
            return await self.do_ghost(interaction, channel, row, ghosted=True)
        if action == "unghost":
            return await self.do_ghost(interaction, channel, row, ghosted=False)
        if action == "permit":
            return await interaction.response.send_modal(
                UserOrRoleModal(self, channel, "permit", "Permit User/Role")
            )
        if action == "reject":
            return await interaction.response.send_modal(
                UserOrRoleModal(self, channel, "reject", "Reject User/Role")
            )
        if action == "invite":
            return await interaction.response.send_modal(InviteModal(self, channel))

    # ---------------------------------------------------------
    #   Actions (shared by dropdowns AND slash commands)
    # ---------------------------------------------------------

    async def do_rename(self, interaction: discord.Interaction, channel: discord.VoiceChannel, name: str):
        name = name.strip()[:100]
        if not name:
            return await interaction.response.send_message("🚫 Name can't be empty.", ephemeral=True)
        try:
            await channel.edit(name=name, reason=f"VoiceMaster: renamed by {interaction.user}")
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"⚠️ Couldn't rename: {e}", ephemeral=True)
        await db.save_profile(guild_id=interaction.guild.id, user_id=interaction.user.id, channel_name=name)
        e = base_embed(SUCCESS_COLOR)
        e.description = f"✏️ Channel renamed to **{name}**."
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def do_limit(self, interaction: discord.Interaction, channel: discord.VoiceChannel, limit: int):
        try:
            await channel.edit(user_limit=limit, reason=f"VoiceMaster: limit set by {interaction.user}")
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"⚠️ Couldn't set limit: {e}", ephemeral=True)
        await db.save_profile(guild_id=interaction.guild.id, user_id=interaction.user.id, user_limit=limit)
        e = base_embed(SUCCESS_COLOR)
        e.description = f"👥 User limit set to **{limit if limit else 'unlimited'}**."
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def do_status(self, interaction: discord.Interaction, channel: discord.VoiceChannel, status: str):
        status = status.strip()[:500]
        # No public wrapper exists on VoiceChannel for this endpoint yet, so we
        # call the route directly the same way discord.py does internally.
        route = discord.http.Route(
            "PUT", "/channels/{channel_id}/voice-status", channel_id=channel.id
        )
        try:
            await channel._state.http.request(route, json={"status": status})
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"⚠️ Couldn't set status: {e}", ephemeral=True)

        e = base_embed(SUCCESS_COLOR)
        e.description = f"💬 Status set to **{status}**." if status else "💬 Status cleared."
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def do_bitrate(self, interaction: discord.Interaction, channel: discord.VoiceChannel, kbps: int):
        guild = channel.guild
        max_kbps = 384 if "VIP_REGIONS" in guild.features else (
            128 if guild.premium_tier >= 2 else (96 if guild.premium_tier >= 1 else 96)
        )
        kbps = max(8, min(kbps, max_kbps))
        try:
            await channel.edit(bitrate=kbps * 1000, reason=f"VoiceMaster: bitrate set by {interaction.user}")
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"⚠️ Couldn't set bitrate: {e}", ephemeral=True)
        e = base_embed(SUCCESS_COLOR)
        e.description = f"🎚️ Bitrate set to **{kbps} kbps**."
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def do_region(self, interaction: discord.Interaction, channel: discord.VoiceChannel, region_id: str):
        rtc_region = None if region_id == "automatic" else region_id
        try:
            await channel.edit(rtc_region=rtc_region, reason=f"VoiceMaster: region set by {interaction.user}")
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"⚠️ Couldn't set region: {e}", ephemeral=True)
        label = dict(VOICE_REGIONS).get(region_id, region_id)
        e = base_embed(SUCCESS_COLOR)
        e.description = f"🌐 Voice region set to **{label}**."
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def do_lock(self, interaction: discord.Interaction, channel: discord.VoiceChannel, row: dict, *, locked: bool):
        everyone = channel.guild.default_role
        overwrite = channel.overwrites_for(everyone)
        overwrite.connect = False if locked else None
        try:
            await channel.set_permissions(
                everyone, overwrite=overwrite, reason=f"VoiceMaster: {'locked' if locked else 'unlocked'} by {interaction.user}"
            )
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"⚠️ Couldn't update lock state: {e}", ephemeral=True)

        await db.update_temp_channel(channel_id=channel.id, locked=locked)
        await db.save_profile(guild_id=interaction.guild.id, user_id=interaction.user.id, locked=locked)
        e = base_embed(SUCCESS_COLOR)
        e.description = f"{'🔒 Channel locked.' if locked else '🔓 Channel unlocked.'}"
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def do_ghost(self, interaction: discord.Interaction, channel: discord.VoiceChannel, row: dict, *, ghosted: bool):
        everyone = channel.guild.default_role
        overwrite = channel.overwrites_for(everyone)
        overwrite.view_channel = False if ghosted else None
        try:
            await channel.set_permissions(
                everyone, overwrite=overwrite, reason=f"VoiceMaster: {'ghosted' if ghosted else 'unghosted'} by {interaction.user}"
            )
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"⚠️ Couldn't update visibility: {e}", ephemeral=True)

        await db.update_temp_channel(channel_id=channel.id, ghosted=ghosted)
        await db.save_profile(guild_id=interaction.guild.id, user_id=interaction.user.id, ghosted=ghosted)
        e = base_embed(SUCCESS_COLOR)
        e.description = f"{'👻 Channel is now invisible.' if ghosted else '👁️ Channel is now visible.'}"
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def do_permit_reject(
        self, interaction: discord.Interaction, channel: discord.VoiceChannel, action: str, raw_target: str
    ):
        target = self.resolve_member_or_role(interaction.guild, raw_target)
        if not target:
            return await interaction.response.send_message(
                f"🚫 Couldn't find a user or role matching `{raw_target}`.", ephemeral=True
            )

        overwrite = channel.overwrites_for(target)
        if action == "permit":
            overwrite.connect = True
            overwrite.view_channel = True
        else:
            overwrite.connect = False
            if isinstance(target, discord.Member) and target.voice and target.voice.channel == channel:
                try:
                    await target.move_to(None, reason=f"VoiceMaster: rejected by {interaction.user}")
                except discord.HTTPException:
                    pass

        try:
            await channel.set_permissions(
                target, overwrite=overwrite, reason=f"VoiceMaster: {action} by {interaction.user}"
            )
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"⚠️ Couldn't update permissions: {e}", ephemeral=True)

        profile_kwargs = {}
        if action == "permit":
            profile = await db.get_profile(guild_id=interaction.guild.id, user_id=interaction.user.id) or {}
            ids = set(int(i) for i in (profile.get("permitted_ids") or []))
            ids.add(target.id)
            profile_kwargs["permitted_ids"] = list(ids)
        else:
            profile = await db.get_profile(guild_id=interaction.guild.id, user_id=interaction.user.id) or {}
            ids = set(int(i) for i in (profile.get("rejected_ids") or []))
            ids.add(target.id)
            profile_kwargs["rejected_ids"] = list(ids)
        await db.save_profile(guild_id=interaction.guild.id, user_id=interaction.user.id, **profile_kwargs)

        e = base_embed(SUCCESS_COLOR)
        verb = "✅ Permitted" if action == "permit" else "⛔ Rejected"
        e.description = f"{verb} {target.mention} {'to' if action == 'permit' else 'from'} this channel."
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def do_claim(self, interaction: discord.Interaction):
        member = interaction.user
        voice_state = member.voice
        if not voice_state or not voice_state.channel:
            return await interaction.response.send_message(
                "🚫 You need to be in a temp voice channel to claim it.", ephemeral=True
            )

        channel = voice_state.channel
        row = await db.get_temp_channel(channel_id=channel.id)
        if not row:
            return await interaction.response.send_message(
                "🚫 This isn't a VoiceMaster temp channel.", ephemeral=True
            )

        current_owner = interaction.guild.get_member(int(row["owner_id"]))
        if current_owner and current_owner in channel.members:
            return await interaction.response.send_message(
                f"🚫 {current_owner.mention} is still in the channel — you can't claim it.", ephemeral=True
            )

        try:
            if current_owner:
                await channel.set_permissions(current_owner, overwrite=None, reason="VoiceMaster: claimed")
            await channel.set_permissions(
                member, manage_channels=True, move_members=True, reason=f"VoiceMaster: claimed by {member}"
            )
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"⚠️ Couldn't update permissions: {e}", ephemeral=True)

        order = [uid for uid in (row.get("join_order") or []) if uid != member.id]
        await db.update_temp_channel(channel_id=channel.id, owner_id=member.id, join_order=order)

        e = base_embed(SUCCESS_COLOR)
        e.description = f"👑 {member.mention} has claimed ownership of this channel."
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def do_transfer(self, interaction: discord.Interaction, channel: discord.VoiceChannel, raw_target: str):
        target = self.resolve_member_or_role(interaction.guild, raw_target)
        if not isinstance(target, discord.Member):
            return await interaction.response.send_message(
                f"🚫 Couldn't find a member matching `{raw_target}`.", ephemeral=True
            )
        if target.bot:
            return await interaction.response.send_message("🚫 Can't transfer ownership to a bot.", ephemeral=True)
        if not target.voice or target.voice.channel != channel:
            return await interaction.response.send_message(
                f"🚫 {target.mention} needs to be in the channel to receive ownership.", ephemeral=True
            )

        try:
            await channel.set_permissions(interaction.user, overwrite=None, reason="VoiceMaster: ownership transferred")
            await channel.set_permissions(
                target, manage_channels=True, move_members=True, reason=f"VoiceMaster: transferred by {interaction.user}"
            )
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"⚠️ Couldn't update permissions: {e}", ephemeral=True)

        row = await db.get_temp_channel(channel_id=channel.id) or {}
        order = [uid for uid in (row.get("join_order") or []) if uid != target.id]
        await db.update_temp_channel(channel_id=channel.id, owner_id=target.id, join_order=order)

        e = base_embed(SUCCESS_COLOR)
        e.description = f"🔄 Ownership transferred to {target.mention}."
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def do_load_settings(self, interaction: discord.Interaction):
        channel, row = await self._require_ownership(interaction)
        if not channel:
            return

        profile = await db.get_profile(guild_id=interaction.guild.id, user_id=interaction.user.id)
        if not profile:
            e = base_embed(DANGER_COLOR)
            e.description = "📭 You don't have any saved settings yet. Change some settings first."
            return await interaction.response.send_message(embed=e, ephemeral=True)

        applied = []
        edit_kwargs = {}
        if profile.get("channel_name"):
            edit_kwargs["name"] = profile["channel_name"][:100]
            applied.append("name")
        if profile.get("user_limit") is not None:
            edit_kwargs["user_limit"] = profile["user_limit"]
            applied.append("limit")

        if edit_kwargs:
            try:
                await channel.edit(**edit_kwargs, reason=f"VoiceMaster: profile loaded by {interaction.user}")
            except discord.HTTPException as e:
                return await interaction.response.send_message(f"⚠️ Couldn't apply saved settings: {e}", ephemeral=True)

        everyone = channel.guild.default_role
        overwrite = channel.overwrites_for(everyone)
        if profile.get("locked") is not None:
            overwrite.connect = False if profile["locked"] else None
            applied.append("lock state")
        if profile.get("ghosted") is not None:
            overwrite.view_channel = False if profile["ghosted"] else None
            applied.append("ghost state")
        try:
            await channel.set_permissions(everyone, overwrite=overwrite, reason="VoiceMaster: profile loaded")
        except discord.HTTPException:
            pass

        for uid in profile.get("permitted_ids") or []:
            target = interaction.guild.get_member(int(uid)) or interaction.guild.get_role(int(uid))
            if target:
                ow = channel.overwrites_for(target)
                ow.connect = True
                ow.view_channel = True
                try:
                    await channel.set_permissions(target, overwrite=ow, reason="VoiceMaster: profile loaded")
                except discord.HTTPException:
                    pass
        if profile.get("permitted_ids"):
            applied.append("permit list")

        for uid in profile.get("rejected_ids") or []:
            target = interaction.guild.get_member(int(uid)) or interaction.guild.get_role(int(uid))
            if target:
                ow = channel.overwrites_for(target)
                ow.connect = False
                try:
                    await channel.set_permissions(target, overwrite=ow, reason="VoiceMaster: profile loaded")
                except discord.HTTPException:
                    pass
        if profile.get("rejected_ids"):
            applied.append("reject list")

        await db.update_temp_channel(
            channel_id=channel.id,
            locked=profile.get("locked", row.get("locked", False)),
            ghosted=profile.get("ghosted", row.get("ghosted", False)),
        )

        e = base_embed(SUCCESS_COLOR)
        e.description = (
            f"📥 Loaded your saved settings ({', '.join(applied)})." if applied
            else "📭 Your saved profile is empty — nothing to apply."
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ---------------------------------------------------------
    #   Invite (dropdown-only action, matches reference UI)
    # ---------------------------------------------------------

    async def do_invite(self, interaction: discord.Interaction, channel: discord.VoiceChannel, member: discord.Member):
        ow = channel.overwrites_for(member)
        ow.connect = True
        ow.view_channel = True
        try:
            await channel.set_permissions(member, overwrite=ow, reason=f"VoiceMaster: invited by {interaction.user}")
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"⚠️ Couldn't invite: {e}", ephemeral=True)

        e = base_embed(SUCCESS_COLOR)
        e.description = f"📨 Invited {member.mention} to {channel.mention}."
        await interaction.response.send_message(embed=e, ephemeral=True)
        try:
            dm_embed = base_embed(BOT_COLOR)
            dm_embed.description = (
                f"🔔 {interaction.user.mention} invited you to join their voice channel "
                f"**{channel.name}** in **{interaction.guild.name}**."
            )
            await member.send(embed=dm_embed)
        except discord.HTTPException:
            pass

    # ---------------------------------------------------------
    #   Generic error handler for the whole /voice group
    # ---------------------------------------------------------

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        log.exception("VoiceMaster command error", exc_info=error)
        msg = "⚠️ Something went wrong."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceMaster(bot))
