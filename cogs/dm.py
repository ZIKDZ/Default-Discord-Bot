import logging
import re

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

HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")
USER_TOKEN_RE = re.compile(r"<@!?(\d+)>|(\d{15,20})")


def is_staff(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    perms = interaction.user.guild_permissions
    role_ids = [r.id for r in interaction.user.roles]
    return perms.administrator or any(rid in EXTRA_ALLOWED_ROLE_IDS for rid in role_ids)


def parse_color(color: str | None) -> tuple[int | None, bool]:
    """Returns (value, ok). ok=False means a color string was given but invalid."""
    if not color:
        return None, True
    color = color.strip()
    if not HEX_RE.match(color):
        return None, False
    return int(color.lstrip("#"), 16), True


def parse_recipient_ids(raw: str | None) -> list[int]:
    """Extract user IDs from @mentions/raw IDs, comma/space/newline separated."""
    ids: list[int] = []
    seen: set[int] = set()
    for mention_id, plain_id in USER_TOKEN_RE.findall(raw or ""):
        uid = int(mention_id or plain_id)
        if uid not in seen:
            seen.add(uid)
            ids.append(uid)
    return ids


def parse_meta_line(raw: str) -> tuple[str | None, str | None, bool]:
    """
    Parses the combined 'Color / Save as preset' modal line.
    Accepts things like:  #5865F2 preset:welcome   |   preset: welcome   |   ff0000
    Returns (color_str, preset_name, ok).
    """
    if not raw or not raw.strip():
        return None, None, True

    color_str = None
    preset_name = None

    preset_match = re.search(r"preset\s*:\s*([a-zA-Z0-9_\-]+)", raw, flags=re.IGNORECASE)
    if preset_match:
        preset_name = preset_match.group(1).strip().lower()
        raw = raw[: preset_match.start()] + raw[preset_match.end():]

    remainder = raw.strip()
    if remainder:
        color_str = remainder.split()[0]
        _, ok = parse_color(color_str)
        if not ok:
            return None, None, False

    return color_str, preset_name, True


def build_embed(
    *,
    title: str | None,
    description: str | None,
    color: int | None,
    footer: str | None = None,
) -> discord.Embed | None:
    if not any([title, description]):
        return None
    embed = discord.Embed(
        title=title[:256] if title else None,
        description=description[:4096] if description else None,
        color=color if color is not None else 0x2B2D31,
    )
    if footer:
        embed.set_footer(text=footer[:2048])
    return embed


async def _send_to_one(
    interaction: discord.Interaction,
    *,
    target: discord.Member | discord.User,
    message: str | None,
    embed: discord.Embed | None,
    kind: str,
    preset_name: str | None,
) -> tuple[bool, str | None]:
    error = None
    success = True
    try:
        dm_channel = await target.create_dm()
        await dm_channel.send(content=message, embed=embed)
    except discord.Forbidden:
        success = False
        error = "forbidden"
    except Exception as e:
        success = False
        error = str(e)[:500]
        log.exception("Failed to send DM")

    await db.log_dm(
        guild_id=interaction.guild.id if interaction.guild else None,
        sender_id=interaction.user.id,
        target_id=target.id,
        kind=kind,
        preset_name=preset_name,
        content=message,
        embed_json=embed.to_dict() if embed else None,
        success=success,
        error=error,
    )
    return success, error


# ================================
#   The single popup (content only — recipients are already resolved)
# ================================

class SendDMModal(discord.ui.Modal, title="Send a DM"):
    def __init__(self, *, targets: list[discord.Member | discord.User]):
        super().__init__()
        self.targets = targets

    message_input = discord.ui.TextInput(
        label="Message (optional if using title/description)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=2000,
    )
    title_input = discord.ui.TextInput(
        label="Embed Title (optional)",
        style=discord.TextStyle.short,
        required=False,
        max_length=256,
    )
    description_input = discord.ui.TextInput(
        label="Embed Description (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1024,
    )
    footer_input = discord.ui.TextInput(
        label="Embed Footer (optional)",
        style=discord.TextStyle.short,
        required=False,
        max_length=2048,
    )
    meta_input = discord.ui.TextInput(
        label="Color / Save as preset (optional)",
        style=discord.TextStyle.short,
        placeholder="Ex: #5865F2, ff0000, preset:welcome, or #5865F2 preset:welcome",
        required=False,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction):
        targets = self.targets
        message = str(self.message_input.value) or None
        title = str(self.title_input.value) or None
        description = str(self.description_input.value) or None
        footer = str(self.footer_input.value) or None

        if not message and not title and not description:
            return await interaction.response.send_message(
                "❌ Provide a message and/or an embed title/description.", ephemeral=True
            )

        color_str, preset_name, ok = parse_meta_line(str(self.meta_input.value))
        if not ok:
            return await interaction.response.send_message(
                "❌ Invalid color. Use a hex code like `#5865F2` or `ff0000`.", ephemeral=True
            )
        parsed_color, _ = parse_color(color_str)

        embed = build_embed(title=title, description=description, color=parsed_color, footer=footer)

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        preset_note = ""
        if preset_name and embed and guild:
            saved = await db.create_preset(
                guild_id=guild.id,
                name=preset_name,
                title=title,
                description=description,
                color=parsed_color,
                image_url=None,
                footer=footer,
                created_by=interaction.user.id,
            )
            preset_note = (
                f"✅ Preset `{preset_name}` saved.\n" if saved
                else "⚠️ Couldn't save preset (database unavailable).\n"
            )
        elif preset_name and not embed:
            preset_note = "⚠️ Preset not saved — presets need an embed title/description.\n"

        kind = "embed" if embed else "plain"
        sent, failed = [], []
        for target in targets:
            success, _ = await _send_to_one(
                interaction, target=target, message=message, embed=embed,
                kind=kind, preset_name=preset_name,
            )
            (sent if success else failed).append(target)

        lines = [preset_note] if preset_note else []
        if sent:
            lines.append(f"✅ Sent to: {', '.join(u.mention for u in sent)}")
        if failed:
            lines.append(f"❌ Couldn't DM: {', '.join(u.mention for u in failed)} (DMs closed or error)")

        await interaction.followup.send("\n".join(lines) or "Nothing sent.", ephemeral=True)


# ================================
#   Preset autocomplete + quick management commands
# ================================

async def preset_name_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    if not interaction.guild:
        return []
    presets = await db.list_presets(guild_id=interaction.guild.id)
    return [
        app_commands.Choice(name=p["name"], value=p["name"])
        for p in presets
        if current.lower() in p["name"].lower()
    ][:25]


class DM(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- /dm — single recipient, native picker ----------

    @app_commands.command(name="dm", description="Send a DM (message and/or embed) to one member")
    @app_commands.describe(member="Who to DM")
    async def dm(self, interaction: discord.Interaction, member: discord.Member):
        if not is_staff(interaction):
            return await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        if member.bot:
            return await interaction.response.send_message("❌ Cannot DM bots.", ephemeral=True)

        await interaction.response.send_modal(SendDMModal(targets=[member]))

    # ---------- /dm-bulk — multiple recipients ----------

    @app_commands.command(name="dm-bulk", description="Send a DM (message and/or embed) to several members at once")
    @app_commands.describe(recipients="@mentions or IDs, separated by spaces or commas")
    async def dm_bulk(self, interaction: discord.Interaction, recipients: str):
        if not is_staff(interaction):
            return await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)

        ids = parse_recipient_ids(recipients)
        if not ids:
            return await interaction.response.send_message(
                "❌ Couldn't find any valid @mentions or user IDs in `recipients`.", ephemeral=True
            )

        guild = interaction.guild
        targets: list[discord.Member] = []
        for uid in ids:
            m = guild.get_member(uid) if guild else None
            if m and not m.bot:
                targets.append(m)

        if not targets:
            return await interaction.response.send_message(
                "❌ None of the recipients could be resolved to valid (non-bot) server members.", ephemeral=True
            )

        await interaction.response.send_modal(SendDMModal(targets=targets))

    # ---------- quick-send a saved preset ----------

    @app_commands.command(name="dm-preset-send", description="Send a saved DM preset to a member")
    @app_commands.describe(member="User to DM", preset_name="Saved preset name")
    @app_commands.autocomplete(preset_name=preset_name_autocomplete)
    async def dm_preset_send(self, interaction: discord.Interaction, member: discord.Member, preset_name: str):
        if not is_staff(interaction):
            return await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        if member.bot:
            return await interaction.response.send_message("❌ Cannot DM bots.", ephemeral=True)

        preset = await db.get_preset(guild_id=interaction.guild.id, name=preset_name)
        if not preset:
            return await interaction.response.send_message(f"❌ No preset named `{preset_name}` found.", ephemeral=True)

        embed = build_embed(
            title=preset.get("title"),
            description=preset.get("description"),
            color=preset.get("color"),
            footer=preset.get("footer"),
        )

        await interaction.response.defer(ephemeral=True)
        success, error = await _send_to_one(
            interaction, target=member, message=None, embed=embed, kind="preset", preset_name=preset["name"]
        )
        if success:
            await interaction.followup.send(f"✅ Preset `{preset_name}` sent to {member.mention}.", ephemeral=True)
        elif error == "forbidden":
            await interaction.followup.send(f"❌ Couldn't DM {member.mention} (their DMs are closed).", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Failed to send DM: {error}", ephemeral=True)

    @app_commands.command(name="dm-preset-list", description="List saved DM presets")
    async def dm_preset_list(self, interaction: discord.Interaction):
        if not is_staff(interaction):
            return await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        presets = await db.list_presets(guild_id=interaction.guild.id)
        if not presets:
            return await interaction.followup.send("No presets saved yet.", ephemeral=True)
        names = ", ".join(f"`{p['name']}`" for p in presets)
        await interaction.followup.send(f"**Saved presets:** {names}", ephemeral=True)

    @app_commands.command(name="dm-preset-delete", description="Delete a DM preset")
    @app_commands.describe(name="Preset name to delete")
    @app_commands.autocomplete(name=preset_name_autocomplete)
    async def dm_preset_delete(self, interaction: discord.Interaction, name: str):
        if not is_staff(interaction):
            return await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        ok = await db.delete_preset(guild_id=interaction.guild.id, name=name)
        if ok:
            await interaction.followup.send(f"✅ Preset `{name.strip().lower()}` deleted.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Couldn't delete preset (database unavailable).", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DM(bot))