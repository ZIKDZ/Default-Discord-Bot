from discord import app_commands
from discord.ext import commands
import discord
import logging

log = logging.getLogger(__name__)

# ==========================================================
EXTRA_ALLOWED_ROLE_IDS = [
    1141838188650967200,
]
# ==========================================================


class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Check if the bot is online")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message("Pong!")

    @app_commands.command(name="say", description="Make the bot say something (staff only)")
    @app_commands.describe(text="What should I say?")
    async def say(self, interaction: discord.Interaction, text: str):

        perms = interaction.user.guild_permissions
        user_role_ids = [role.id for role in interaction.user.roles]

        # ✅ Admins automatically allowed
        is_admin = perms.administrator

        # ✅ Extra mod roles allowed
        has_extra_role = any(role_id in EXTRA_ALLOWED_ROLE_IDS for role_id in user_role_ids)

        if not (is_admin or has_extra_role):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command.",
                ephemeral=True
            )

        log.info(f"/say used by {interaction.user} in {interaction.guild} | {text}")

        await interaction.response.send_message("✅ Message sent.", ephemeral=True)
        await interaction.channel.send(text)


async def setup(bot):
    await bot.add_cog(General(bot))