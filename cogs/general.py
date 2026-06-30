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

        is_admin = perms.administrator
        has_extra_role = any(role_id in EXTRA_ALLOWED_ROLE_IDS for role_id in user_role_ids)

        if not (is_admin or has_extra_role):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command.",
                ephemeral=True
            )

        log.info(f"/say used by {interaction.user} in {interaction.guild} | {text}")

        await interaction.response.send_message("✅ Message sent.", ephemeral=True)
        await interaction.channel.send(text)

    @app_commands.command(name="dm", description="Send a DM to a user (staff only)")
    @app_commands.describe(
        user="User to send the DM to",
        message="Message to send"
    )
    async def dm(self, interaction: discord.Interaction, user: discord.Member, message: str):

        perms = interaction.user.guild_permissions
        user_role_ids = [role.id for role in interaction.user.roles]

        is_admin = perms.administrator
        has_extra_role = any(role_id in EXTRA_ALLOWED_ROLE_IDS for role_id in user_role_ids)

        if not (is_admin or has_extra_role):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command.",
                ephemeral=True
            )

        if user.bot:
            return await interaction.response.send_message(
                "❌ Cannot DM bots.",
                ephemeral=True
            )

        try:
            embed = discord.Embed(
                title="Official Esport T-Shirt Signup",
                color=12845311,
            )

            embed.add_field(
                name="Signup Link",
                value="https://docs.google.com/forms/d/1wAleiPWx3qz_2j9291BEbeOMTeBa4TkhXdQ2DMsT7KE/edit",
                inline=False
            )

            embed.add_field(
                name="💳 CCP Payment Info",
                value="**Full Name:** Zaoui Ilias Kamel\n"
                      "**CCP:** 42893906\n"
                      "***Clé:*** 07\n"
                      "**Baridimob RIP:** ```00799999004289390646```",
                inline=False
            )

            embed.add_field(
                name="Signup Deadline",
                value="**Tuesday 11th of this month at 23:59**",
                inline=False
            )

            embed.add_field(
                name="Payment Deadline",
                value="**Thursday 12th of this month at 17:00**",
                inline=False
            )

            embed.set_footer(text="")

            dm_channel = await user.create_dm()

            # Send DM with mention + embed
            await dm_channel.send(
                content=f"Hello {user.mention}, please check the signup info below!",
                embed=embed
            )

            log.info(f"/dm used by {interaction.user} -> {user} | {message}")

            await interaction.response.send_message(
                f"✅ DM sent to {user.mention}",
                ephemeral=True
            )

        except discord.Forbidden:
            log.warning(f"❌ Could not send DM to {user} (DMs closed).")
            await interaction.response.send_message(
                f"❌ I can't DM {user.mention} (their DMs are closed).",
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(General(bot))