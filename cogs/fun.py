import discord
from discord import app_commands
from discord.ext import commands
import asyncio

# ==============================
# CONFIG — EDIT THIS ONLY
EXTRA_STAFF_ROLE_IDS = [
    1141838188650967200,
]

MAX_SPAM = 50
SPAM_DELAY = 0.01
COOLDOWN_SECONDS = 15
# ==============================


def is_staff(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False

    perms = interaction.user.guild_permissions
    user_roles = [role.id for role in interaction.user.roles]

    return perms.administrator or any(rid in EXTRA_STAFF_ROLE_IDS for rid in user_roles)


class Fun(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    staff_only = app_commands.check(is_staff)

    @app_commands.command(name="spam", description="Staff only fun tag spam")
    @staff_only
    @app_commands.describe(
        member="User to spam",
        amount="How many times (max 50)"
    )
    @app_commands.checks.cooldown(1, COOLDOWN_SECONDS)
    async def spam(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, 1, MAX_SPAM]
    ):
        # silent=True prevents the visible "Bot is thinking..." state (more anonymous)
        await interaction.response.defer(ephemeral=True, thinking=False)

        for _ in range(amount):
            await interaction.channel.send(member.mention)
            await asyncio.sleep(SPAM_DELAY)

        # Ephemeral confirmation (only the staff user sees it)
        await interaction.followup.send("✅ Done.", ephemeral=True)

    @spam.error
    async def spam_error(self, interaction: discord.Interaction, error: Exception):
        # Pick message
        if isinstance(error, app_commands.CheckFailure):
            msg = "❌ Staff only."
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⏳ Chill… Try again in {round(error.retry_after, 1)}s"
        else:
            msg = "⚠️ Something went wrong."

        # If we already responded/deferred, use followup. Otherwise response.
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))