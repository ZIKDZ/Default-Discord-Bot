from discord import app_commands
from discord.ext import commands
import logging

log = logging.getLogger(__name__)

class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Check if the bot is online")
    async def ping(self, interaction):
        log.info(f"/ping used by {interaction.user}")
        await interaction.response.send_message("Pong!")

async def setup(bot):
    await bot.add_cog(General(bot))
