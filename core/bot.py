import logging
import discord
from discord.ext import commands
from core.config import Config
import os

log = logging.getLogger(__name__)

class BaseBot(commands.Bot):
    def __init__(self, intents: discord.Intents):
        # Load configuration (secrets, toggles, etc.)
        self.config = Config()

        # Initialize the bot
        super().__init__(command_prefix=None, intents=intents)

    async def setup_hook(self):
        log.info("Bot starting...")

        # Load all cogs first
        await self.load_cogs()

        # Sync all slash commands globally (one network call)
        await self.tree.sync()
        log.info("Slash commands synced globally!")

    async def load_cogs(self):
        """Automatically load all .py cogs in the cogs/ folder."""
        cogs_dir = os.path.join(os.path.dirname(__file__), "..", "cogs")
        for filename in os.listdir(cogs_dir):
            if filename.endswith(".py") and not filename.startswith("__"):
                cog_name = f"cogs.{filename[:-3]}"
                await self.load_extension(cog_name)
                log.info(f"Loaded {cog_name}")

    async def on_ready(self):
        log.info(f"✅ Logged in as {self.user}!")
