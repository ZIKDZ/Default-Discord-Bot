import json
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

# ==========================================================
# EDIT THIS SECTION ONLY (defaults — can be overridden per-guild with /autorole)
DEFAULT_HUMAN_ROLE_ID = 1257032396503519334
DEFAULT_BOT_ROLE_ID = 1257142027930570903

EXTRA_STAFF_ROLE_IDS = [
    1141838188650967200,
]
# ==========================================================

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "autorole.json")


def is_staff(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    perms = interaction.user.guild_permissions
    role_ids = [r.id for r in interaction.user.roles]
    return perms.administrator or any(rid in EXTRA_STAFF_ROLE_IDS for rid in role_ids)


def _load_all() -> dict:
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            content = f.read().strip()
            return json.loads(content) if content else {}
    except FileNotFoundError:
        return {}
    except Exception:
        log.exception("Failed to load autorole config, starting fresh")
        return {}


def _save_all(data: dict) -> None:
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


class AutoRole(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._config = _load_all()

    # ---------- config helpers ----------

    def get_guild_config(self, guild_id: int) -> dict:
        cfg = self._config.get(str(guild_id))
        if cfg is None:
            cfg = {
                "enabled": True,
                "human_role_id": DEFAULT_HUMAN_ROLE_ID,
                "bot_role_id": DEFAULT_BOT_ROLE_ID,
            }
        return cfg

    def set_guild_config(self, guild_id: int, **updates) -> dict:
        cfg = self.get_guild_config(guild_id)
        cfg.update(updates)
        self._config[str(guild_id)] = cfg
        _save_all(self._config)
        return cfg

    # ---------- listener ----------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        cfg = self.get_guild_config(guild.id)

        if not cfg.get("enabled", True):
            return

        role_id = cfg.get("bot_role_id") if member.bot else cfg.get("human_role_id")
        if not role_id:
            return

        role = guild.get_role(int(role_id))
        if not role:
            log.warning("Autorole: configured role %s not found in guild %s", role_id, guild.id)
            return

        me = guild.me
        if not me or not me.guild_permissions.manage_roles:
            log.warning("Autorole: missing Manage Roles permission in guild %s", guild.id)
            return

        if role >= me.top_role:
            log.warning(
                "Autorole: role %s is above or equal to my top role in guild %s", role.id, guild.id
            )
            return

        try:
            await member.add_roles(role, reason="Autorole on join")
            log.info("Autorole: gave %s to %s in %s", role, member, guild)
        except discord.Forbidden:
            log.warning("Autorole: forbidden adding %s to %s in %s", role, member, guild)
        except Exception:
            log.exception("Autorole: failed to add role to %s in %s", member, guild)

    # ---------- commands ----------

    autorole_group = app_commands.Group(
        name="autorole", description="Configure automatic role-on-join (staff only)"
    )

    @autorole_group.command(name="status", description="Show the current autorole setup")
    async def status(self, interaction: discord.Interaction):
        if not is_staff(interaction):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        cfg = self.get_guild_config(interaction.guild.id)
        human_role = interaction.guild.get_role(int(cfg["human_role_id"])) if cfg.get("human_role_id") else None
        bot_role = interaction.guild.get_role(int(cfg["bot_role_id"])) if cfg.get("bot_role_id") else None

        embed = discord.Embed(
            title="⚙️ Autorole Setup",
            color=0x5865F2,
        )
        embed.add_field(name="Enabled", value="✅ Yes" if cfg.get("enabled", True) else "❌ No", inline=False)
        embed.add_field(name="Human role", value=human_role.mention if human_role else f"⚠️ Not found (`{cfg.get('human_role_id')}`)", inline=True)
        embed.add_field(name="Bot role", value=bot_role.mention if bot_role else f"⚠️ Not found (`{cfg.get('bot_role_id')}`)", inline=True)

        me = interaction.guild.me
        if me and not me.guild_permissions.manage_roles:
            embed.add_field(
                name="⚠️ Warning",
                value="I don't have the **Manage Roles** permission, autorole won't work.",
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @autorole_group.command(name="set-human", description="Set the role given to human members on join")
    @app_commands.describe(role="Role to give to humans")
    async def set_human(self, interaction: discord.Interaction, role: discord.Role):
        if not is_staff(interaction):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        me = interaction.guild.me
        if me and role >= me.top_role:
            return await interaction.response.send_message(
                "⚠️ That role is above or equal to my highest role, I won't be able to assign it. "
                "Saved anyway, but move my role above it first.",
                ephemeral=True,
            )

        self.set_guild_config(interaction.guild.id, human_role_id=role.id)
        await interaction.response.send_message(f"✅ Human autorole set to {role.mention}.", ephemeral=True)

    @autorole_group.command(name="set-bot", description="Set the role given to bot members on join")
    @app_commands.describe(role="Role to give to bots")
    async def set_bot(self, interaction: discord.Interaction, role: discord.Role):
        if not is_staff(interaction):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        me = interaction.guild.me
        if me and role >= me.top_role:
            return await interaction.response.send_message(
                "⚠️ That role is above or equal to my highest role, I won't be able to assign it. "
                "Saved anyway, but move my role above it first.",
                ephemeral=True,
            )

        self.set_guild_config(interaction.guild.id, bot_role_id=role.id)
        await interaction.response.send_message(f"✅ Bot autorole set to {role.mention}.", ephemeral=True)

    @autorole_group.command(name="enable", description="Turn autorole on")
    async def enable(self, interaction: discord.Interaction):
        if not is_staff(interaction):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        self.set_guild_config(interaction.guild.id, enabled=True)
        await interaction.response.send_message("✅ Autorole enabled.", ephemeral=True)

    @autorole_group.command(name="disable", description="Turn autorole off")
    async def disable(self, interaction: discord.Interaction):
        if not is_staff(interaction):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        self.set_guild_config(interaction.guild.id, enabled=False)
        await interaction.response.send_message("✅ Autorole disabled.", ephemeral=True)

    @autorole_group.command(name="test", description="Give yourself the currently configured human role (test)")
    async def test(self, interaction: discord.Interaction):
        if not is_staff(interaction):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Server only.", ephemeral=True)

        cfg = self.get_guild_config(interaction.guild.id)
        role = interaction.guild.get_role(int(cfg["human_role_id"])) if cfg.get("human_role_id") else None
        if not role:
            return await interaction.response.send_message("⚠️ Human role not found.", ephemeral=True)

        me = interaction.guild.me
        if not me or not me.guild_permissions.manage_roles or role >= me.top_role:
            return await interaction.response.send_message(
                "⚠️ I can't assign that role (missing permission or role hierarchy issue).",
                ephemeral=True,
            )

        try:
            await interaction.user.add_roles(role, reason="Autorole test")
            await interaction.response.send_message(f"✅ Gave you {role.mention}.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoRole(bot))
