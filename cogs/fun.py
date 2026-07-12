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
ROULETTE_STAFF_ONLY = False
ROULETTE_CHAMBERS = 6
ROULETTE_COOLDOWN_SECONDS = 10
ROULETTE_KICK_REASON = "Lost VC roulette 🔫"

ROULETTE_COLOR_SPIN = 0x2B2D31
ROULETTE_COLOR_SURVIVE = 0x57F287
ROULETTE_COLOR_DEATH = 0xED4245
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
        await interaction.response.defer(ephemeral=True, thinking=False)

        for _ in range(amount):
            await interaction.channel.send(member.mention)
            await asyncio.sleep(SPAM_DELAY)

        await interaction.followup.send("✅ Done.", ephemeral=True)

    @spam.error
    async def spam_error(self, interaction: discord.Interaction, error: Exception):
        if isinstance(error, app_commands.CheckFailure):
            msg = "❌ Staff only."
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⏳ Chill… Try again in {round(error.retry_after, 1)}s"
        else:
            msg = "⚠️ Something went wrong."

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

        # No "thinking..." bubble — we send the real message immediately instead of deferring
        spin_embed = discord.Embed(
            title="🔫 Loading the cylinder...",
            description=f"{member.mention} spins the barrel and points it at their own head.",
            color=ROULETTE_COLOR_SPIN,
        )
        spin_embed.set_footer(text=f"Chambers: {ROULETTE_CHAMBERS} • 1 is loaded")
        await interaction.response.send_message(embed=spin_embed)
        msg = await interaction.original_response()

        # Animated spin — edit a couple times for suspense
        spin_frames = ["🔄", "🔃", "🔄"]
        for frame in spin_frames:
            await asyncio.sleep(0.6)
            spin_embed.title = f"{frame} Spinning..."
            await msg.edit(embed=spin_embed)

        await asyncio.sleep(0.8)

        chamber = random.randint(1, ROULETTE_CHAMBERS)
        loaded = (chamber == 1)

        if loaded:
            result_embed = discord.Embed(
                title="💥 BANG!",
                description=f"{member.mention} took the shot and didn't make it.",
                color=ROULETTE_COLOR_DEATH,
            )
            try:
                await member.move_to(None, reason=ROULETTE_KICK_REASON)
                result_embed.add_field(name="Result", value=f"Kicked from **{channel.name}**", inline=False)
            except discord.Forbidden:
                result_embed.add_field(name="Result", value="⚠️ Missing permission to move them.", inline=False)
            except Exception:
                result_embed.add_field(name="Result", value="⚠️ Something went wrong.", inline=False)
        else:
            result_embed = discord.Embed(
                title="🔫 *click.*",
                description=f"{member.mention} survives. Lucky.",
                color=ROULETTE_COLOR_SURVIVE,
            )

        result_embed.set_footer(text=f"Chamber {chamber} of {ROULETTE_CHAMBERS}")
        await msg.edit(embed=result_embed)

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