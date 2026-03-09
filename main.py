import discord
from core.bot import BaseBot
from core.logger import setup_logging  # renamed
import logging

setup_logging()
log = logging.getLogger(__name__)  # get a logger for this file

log.info("Starting bot...")

intents = discord.Intents.default()  # slash commands do not need privileged intents
intents.members = True
bot = BaseBot(intents=intents)

log.info("Running bot...")
bot.run(bot.config.get("DISCORD_TOKEN"))
