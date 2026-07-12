import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import random

# ==============================
# CONFIG — EDIT THIS ONLY
EXTRA_STAFF_ROLE_IDS = [
    1141838188650967200,
]

MAX_SPAM = 50
SPAM_DELAY = 0.01
COOLDOWN_SECONDS = 15

# --- Roulette config ---
ROULETTE_STAFF_ONLY = False       # set True to restrict /roulette to staff too
ROULETTE_CHAMBERS = 6             # 1-in-N odds (6 = classic revolver)
ROULETTE_COOLDOWN_SECONDS = 10
ROULETTE_KICK_REASON = "Lost VC roulette 🔫"
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

    # ---------- VC Roulette ----------

    @app_commands.command(
        name="roulette",
        description=f"Pull the trigger — 1 in {ROULETTE_CHAMBERS} chance you get kicked from VC"
    )
    @app_commands.checks.cooldown(1, ROULETTE_COOLDOWN_SECONDS)
    async def roulette(self, interaction: discord.Interaction):
        if ROULETTE_STAFF_ONLY and not is_staff(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        if not interaction.guild:
            return await interaction.response.send_message(
                "This only works in a server.", ephemeral=True
            )

        member = interaction.user
        voice_state = member.voice
        if not voice_state or not voice_state.channel:
            return await interaction.response.send_message(
                "❌ You need to be in a voice channel to play.", ephemeral=True
            )

        channel = voice_state.channel

        me = interaction.guild.me
        if not me or not me.guild_permissions.move_members:
            return await interaction.response.send_message(
                "⚠️ I need the **Move Members** permission to run this.", ephemeral=True
            )

        await interaction.response.defer(thinking=False)

        # Suspense
        cylinder_msg = await interaction.channel.send(
            f"🔫 {member.mention} spins the cylinder..."
        )
        await asyncio.sleep(1.5)

        chamber = random.randint(1, ROULETTE_CHAMBERS)
        loaded = (chamber == 1)  # 1-in-N chance

        if loaded:
            try:
                await member.move_to(None, reason=ROULETTE_KICK_REASON)
                result = f"💥 **BANG!** {member.mention} got kicked from **{channel.name}**."
            except discord.Forbidden:
                result = f"💥 **BANG!** ...but I don't have permission to move {member.mention}."
            except Exception:
                result = f"💥 **BANG!** ...but something went wrong kicking {member.mention}."
        else:
            result = f"🔫 *click.* {member.mention} survives. Lucky."

        await cylinder_msg.edit(content=result)

    @roulette.error
    async def roulette_error(self, interaction: discord.Interaction, error: Exception):
        if isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⏳ Reload first… Try again in {round(error.retry_after, 1)}s"
        elif isinstance(error, app_commands.CheckFailure):
            msg = "❌ Staff only."
        else:
            msg = "⚠️ Something went wrong."

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))