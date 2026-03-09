import logging
import os

import discord
from discord.ext import commands

from core.config import Config

log = logging.getLogger(__name__)

# If you only use slash commands, this prefix is basically a placeholder.
# It MUST be a string (cannot be None), otherwise discord.py will crash.
DEFAULT_PREFIX = "!"


class BaseBot(commands.Bot):
    def __init__(self, intents: discord.Intents):
        # Load configuration (secrets, toggles, etc.)
        self.config = Config()

        super().__init__(
            command_prefix=DEFAULT_PREFIX,
            intents=intents,
            help_command=None,  # optional: disables the default text help command
        )

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

        if not os.path.isdir(cogs_dir):
            log.warning("Cogs directory not found: %s", cogs_dir)
            return

        for filename in os.listdir(cogs_dir):
            if not filename.endswith(".py") or filename.startswith("__"):
                continue

            cog_name = f"cogs.{filename[:-3]}"
            try:
                await self.load_extension(cog_name)
                log.info("Loaded %s", cog_name)
            except Exception:
                log.exception("Failed to load %s", cog_name)

    async def on_ready(self):
        log.info("✅ Logged in as %s! (ID: %s)", self.user, getattr(self.user, "id", "unknown"))