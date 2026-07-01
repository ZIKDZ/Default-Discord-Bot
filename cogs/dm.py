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


def is_staff(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    perms = interaction.user.guild_permissions
    role_ids = [r.id for r in interaction.user.roles]
    return perms.administrator or any(rid in EXTRA_ALLOWED_ROLE_IDS for rid in role_ids)


def parse_color(color: str | None) -> tuple[int | None, bool]:
    """Returns (value, ok). ok=False means the string was provided but invalid."""
    if not color:
        return None, True
    color = color.strip()
    if not HEX_RE.match(color):
        return None, False
    return int(color.lstrip("#"), 16), True


def build_embed(
    *,
    title: str | None,
    description: str | None,
    color: int | None,
    image_url: str | None,
    footer: str | None,
) -> discord.Embed | None:
    if not any([title, description, image_url]):
        return None
    embed = discord.Embed(
        title=title[:256] if title else None,
        description=description[:4096] if description else None,
        color=color if color is not None else 0x2B2D31,
    )
    if image_url:
        embed.set_image(url=image_url)
    if footer:
        embed.set_footer(text=footer[:2048])
    return embed


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


async def _do_send(
    interaction: discord.Interaction,
    *,
    member: discord.Member,
    message: str | None,
    embed: discord.Embed | None,
    kind: str,
    preset_name: str | None,
):
    """Shared send + log + response logic for both /dm send and /dm preset."""
    error = None
    success = True
    try:
        dm_channel = await member.create_dm()
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
        target_id=member.id,
        kind=kind,
        preset_name=preset_name,
        content=message,
        embed_json=embed.to_dict() if embed else None,
        success=success,
        error=error,
    )

    if success:
        who = f"preset `{preset_name}`" if preset_name else "message"
        log.info(f"/dm ({kind}): {interaction.user} -> {member}")
        await interaction.followup.send(f"✅ Sent {who} to {member.mention}.", ephemeral=True)
    elif error == "forbidden":
        await interaction.followup.send(
            f"❌ Couldn't DM {member.mention} (their DMs are closed).", ephemeral=True
        )
    else:
        await interaction.followup.send(f"❌ Failed to send DM: {error}", ephemeral=True)


class DM(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    dm_group = app_commands.Group(name="dm", description="Send DMs to members (staff only)")
    preset_group = app_commands.Group(
        name="dm-preset", description="Manage DM presets (staff only)"
    )

    # ---------- /dm send ----------

    @dm_group.command(name="send", description="Send a custom message/embed to a member")
    @app_commands.describe(
        member="User to DM",
        message="Plain text content (optional if using title/description)",
        title="Embed title (optional)",
        description="Embed description (optional)",
        color="Hex color like #5865F2 (optional)",
        image_url="Embed image URL (optional)",
        footer="Embed footer text (optional)",
    )
    async def send(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        message: str | None = None,
        title: str | None = None,
        description: str | None = None,
        color: str | None = None,
        image_url: str | None = None,
        footer: str | None = None,
    ):
        if not is_staff(interaction):
            return await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)

        if member.bot:
            return await interaction.response.send_message("❌ Cannot DM bots.", ephemeral=True)

        if not message and not any([title, description, image_url]):
            return await interaction.response.send_message(
                "❌ Provide at least a message or some embed content.", ephemeral=True
            )

        parsed_color, ok = parse_color(color)
        if not ok:
            return await interaction.response.send_message(
                "❌ Invalid color. Use a hex code like `#5865F2`.", ephemeral=True
            )

        embed = build_embed(
            title=title, description=description, color=parsed_color,
            image_url=image_url, footer=footer,
        )

        await interaction.response.defer(ephemeral=True)
        await _do_send(
            interaction, member=member, message=message, embed=embed,
            kind="embed" if embed else "plain", preset_name=None,
        )

    # ---------- /dm preset ----------

    @dm_group.command(name="preset", description="Send a saved DM preset to a member")
    @app_commands.describe(
        member="User to DM",
        preset_name="Saved preset name",
        message="Extra plain text sent alongside the embed (optional)",
    )
    @app_commands.autocomplete(preset_name=preset_name_autocomplete)
    async def send_preset(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        preset_name: str,
        message: str | None = None,
    ):
        if not is_staff(interaction):
            return await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        if member.bot:
            return await interaction.response.send_message("❌ Cannot DM bots.", ephemeral=True)

        preset = await db.get_preset(guild_id=interaction.guild.id, name=preset_name)
        if not preset:
            return await interaction.response.send_message(
                f"❌ No preset named `{preset_name}` found. Use `/dm-preset list` to see saved presets.",
                ephemeral=True,
            )

        embed = build_embed(
            title=preset.get("title"),
            description=preset.get("description"),
            color=preset.get("color"),
            image_url=preset.get("image_url"),
            footer=preset.get("footer"),
        )

        await interaction.response.defer(ephemeral=True)
        await _do_send(
            interaction, member=member, message=message, embed=embed,
            kind="preset", preset_name=preset["name"],
        )

    # ---------- preset management ----------

    @preset_group.command(name="create", description="Create or update a DM preset")
    @app_commands.describe(
        name="Preset name (used to send it later)",
        title="Embed title",
        description="Embed description",
        color="Hex color like #5865F2 (optional)",
        image_url="Embed image URL (optional)",
        footer="Embed footer (optional)",
    )
    async def preset_create(
        self,
        interaction: discord.Interaction,
        name: str,
        title: str | None = None,
        description: str | None = None,
        color: str | None = None,
        image_url: str | None = None,
        footer: str | None = None,
    ):
        if not is_staff(interaction):
            return await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        if not any([title, description, image_url]):
            return await interaction.response.send_message(
                "❌ Provide at least a title, description, or image for the preset.", ephemeral=True
            )

        parsed_color, ok = parse_color(color)
        if not ok:
            return await interaction.response.send_message(
                "❌ Invalid color. Use a hex code like `#5865F2`.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        saved = await db.create_preset(
            guild_id=interaction.guild.id,
            name=name,
            title=title,
            description=description,
            color=parsed_color,
            image_url=image_url,
            footer=footer,
            created_by=interaction.user.id,
        )

        if saved:
            await interaction.followup.send(f"✅ Preset `{name.strip().lower()}` saved.", ephemeral=True)
        else:
            await interaction.followup.send(
                "❌ Couldn't save preset — the database isn't configured or is unreachable.", ephemeral=True
            )

    @preset_group.command(name="list", description="List saved DM presets")
    async def preset_list(self, interaction: discord.Interaction):
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

    @preset_group.command(name="delete", description="Delete a DM preset")
    @app_commands.describe(name="Preset name to delete")
    @app_commands.autocomplete(name=preset_name_autocomplete)
    async def preset_delete(self, interaction: discord.Interaction, name: str):
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
