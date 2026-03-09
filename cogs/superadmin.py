import discord
from discord import app_commands
from discord.ext import commands
import logging

log = logging.getLogger(__name__)

# ==========================================================
# EDIT THIS LIST ONLY
# Only these USER IDs can use the superadmin commands
SUPERADMIN_USER_IDS = {
    502552151663575040,
    773128137932013588,  
}
# ==========================================================


def is_superadmin(interaction: discord.Interaction) -> bool:
    return interaction.user.id in SUPERADMIN_USER_IDS


class SuperAdmin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    superadmin_only = app_commands.check(is_superadmin)

    # ---------- Role management ----------

    @app_commands.command(name="add_role", description="(SuperAdmin) Add a role to a member")
    @superadmin_only
    @app_commands.describe(member="Member to modify", role="Role to add", reason="Optional reason")
    async def add_role(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        role: discord.Role,
        reason: str | None = None,
    ):
        if not interaction.guild:
            return await interaction.response.send_message("This command must be used in a server.", ephemeral=True)

        me = interaction.guild.me  # type: ignore
        if not me or not me.guild_permissions.manage_roles:
            return await interaction.response.send_message(
                "I need the **Manage Roles** permission to do that.",
                ephemeral=True,
            )

        # Bot role hierarchy check
        if role >= me.top_role:
            return await interaction.response.send_message(
                "I can’t add that role because it’s **above or equal to my highest role**.",
                ephemeral=True,
            )

        # Optional: prevent touching the server owner
        if member == interaction.guild.owner:
            return await interaction.response.send_message("I can’t modify the server owner.", ephemeral=True)

        await member.add_roles(role, reason=reason or f"SuperAdmin command by {interaction.user} ({interaction.user.id})")
        log.info(f"SUPERADMIN add_role: {interaction.user} -> {member} +{role} reason={reason!r}")
        await interaction.response.send_message(f"✅ Added {role.mention} to {member.mention}.", ephemeral=True)

    @app_commands.command(name="remove_role", description="(SuperAdmin) Remove a role from a member")
    @superadmin_only
    @app_commands.describe(member="Member to modify", role="Role to remove", reason="Optional reason")
    async def remove_role(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        role: discord.Role,
        reason: str | None = None,
    ):
        if not interaction.guild:
            return await interaction.response.send_message("This command must be used in a server.", ephemeral=True)

        me = interaction.guild.me  # type: ignore
        if not me or not me.guild_permissions.manage_roles:
            return await interaction.response.send_message(
                "I need the **Manage Roles** permission to do that.",
                ephemeral=True,
            )

        if role >= me.top_role:
            return await interaction.response.send_message(
                "I can’t remove that role because it’s **above or equal to my highest role**.",
                ephemeral=True,
            )

        if member == interaction.guild.owner:
            return await interaction.response.send_message("I can’t modify the server owner.", ephemeral=True)

        await member.remove_roles(role, reason=reason or f"SuperAdmin command by {interaction.user} ({interaction.user.id})")
        log.info(f"SUPERADMIN remove_role: {interaction.user} -> {member} -{role} reason={reason!r}")
        await interaction.response.send_message(f"✅ Removed {role.mention} from {member.mention}.", ephemeral=True)

    # ---------- Moderation ----------

    @app_commands.command(name="ban", description="(SuperAdmin) Ban a member")
    @superadmin_only
    @app_commands.describe(member="Member to ban", delete_message_days="Days of messages to delete (0-7)", reason="Optional reason")
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        delete_message_days: app_commands.Range[int, 0, 7] = 0,
        reason: str | None = None,
    ):
        if not interaction.guild:
            return await interaction.response.send_message("This command must be used in a server.", ephemeral=True)

        me = interaction.guild.me  # type: ignore
        if not me or not me.guild_permissions.ban_members:
            return await interaction.response.send_message(
                "I need the **Ban Members** permission to do that.",
                ephemeral=True,
            )

        if member == interaction.guild.owner:
            return await interaction.response.send_message("I can’t ban the server owner.", ephemeral=True)

        # Hierarchy check (bot vs target)
        if member.top_role >= me.top_role:
            return await interaction.response.send_message(
                "I can’t ban this user because their top role is **above or equal to mine**.",
                ephemeral=True,
            )

        await interaction.guild.ban(
            member,
            reason=reason or f"SuperAdmin command by {interaction.user} ({interaction.user.id})",
            delete_message_days=delete_message_days,
        )
        log.info(f"SUPERADMIN ban: {interaction.user} -> {member} days={delete_message_days} reason={reason!r}")
        await interaction.response.send_message(f"✅ Banned {member.mention}.", ephemeral=True)

    @app_commands.command(name="kick", description="(SuperAdmin) Kick a member")
    @superadmin_only
    @app_commands.describe(member="Member to kick", reason="Optional reason")
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str | None = None,
    ):
        if not interaction.guild:
            return await interaction.response.send_message("This command must be used in a server.", ephemeral=True)

        me = interaction.guild.me  # type: ignore
        if not me or not me.guild_permissions.kick_members:
            return await interaction.response.send_message(
                "I need the **Kick Members** permission to do that.",
                ephemeral=True,
            )

        if member == interaction.guild.owner:
            return await interaction.response.send_message("I can’t kick the server owner.", ephemeral=True)

        if member.top_role >= me.top_role:
            return await interaction.response.send_message(
                "I can’t kick this user because their top role is **above or equal to mine**.",
                ephemeral=True,
            )

        await member.kick(reason=reason or f"SuperAdmin command by {interaction.user} ({interaction.user.id})")
        log.info(f"SUPERADMIN kick: {interaction.user} -> {member} reason={reason!r}")
        await interaction.response.send_message(f"✅ Kicked {member.mention}.", ephemeral=True)

    # ---------- Nice error message for non-superadmins ----------
    @add_role.error
    @remove_role.error
    @ban.error
    @kick.error
    async def superadmin_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            return await interaction.response.send_message("❌ SuperAdmin only.", ephemeral=True)
        log.exception("SuperAdmin command error", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("⚠️ Something went wrong.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Something went wrong.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SuperAdmin(bot))