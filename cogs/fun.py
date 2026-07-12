import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import io
import math
import random

from PIL import Image, ImageDraw

# ==============================
# CONFIG — EDIT THIS ONLY
EXTRA_STAFF_ROLE_IDS = [
    1141838188650967200,
]

MAX_SPAM = 50
SPAM_DELAY = 0.01
COOLDOWN_SECONDS = 15

# --- Roulette config ---
ROULETTE_STAFF_ONLY = False        # set True to restrict /roulette to staff too
ROULETTE_CHAMBERS = 6              # 1-in-N odds (6 = classic revolver)
ROULETTE_COOLDOWN_SECONDS = 10
ROULETTE_KICK_REASON = "Lost VC roulette 🔫"
ROULETTE_SPIN_WAIT = 2.6           # seconds to let the spin gif play before revealing

ROULETTE_COLOR_SPIN = 0x2B2D31
ROULETTE_COLOR_SURVIVE = 0x57F287
ROULETTE_COLOR_DEATH = 0xED4245
# ==============================


# ==============================
# Revolver cylinder art (Pillow)
# ==============================

_ART_SIZE = 300
_ART_CENTER = _ART_SIZE // 2
_ART_CYL_RADIUS = 100
_ART_CHAMBER_RADIUS = 22

_ART_BG = (0, 0, 0, 0)  # transparent
_ART_METAL = (90, 94, 102, 255)
_ART_METAL_LIGHT = (140, 144, 152, 255)
_ART_CHAMBER_EMPTY = (30, 31, 34, 255)
_ART_CHAMBER_LOADED = (237, 66, 69, 255)
_ART_OUTLINE = (20, 21, 23, 255)


def _draw_cylinder(
    rotation_deg: float,
    chambers: int,
    loaded_index: int | None = None,
    highlight: bool = False,
) -> Image.Image:
    img = Image.new("RGBA", (_ART_SIZE, _ART_SIZE), _ART_BG)
    draw = ImageDraw.Draw(img)

    # outer cylinder body
    draw.ellipse(
        [
            _ART_CENTER - _ART_CYL_RADIUS - 15,
            _ART_CENTER - _ART_CYL_RADIUS - 15,
            _ART_CENTER + _ART_CYL_RADIUS + 15,
            _ART_CENTER + _ART_CYL_RADIUS + 15,
        ],
        fill=_ART_METAL,
        outline=_ART_OUTLINE,
        width=4,
    )
    # inner bevel ring
    draw.ellipse(
        [
            _ART_CENTER - _ART_CYL_RADIUS - 5,
            _ART_CENTER - _ART_CYL_RADIUS - 5,
            _ART_CENTER + _ART_CYL_RADIUS + 5,
            _ART_CENTER + _ART_CYL_RADIUS + 5,
        ],
        outline=_ART_METAL_LIGHT,
        width=3,
    )

    for i in range(chambers):
        angle = math.radians(rotation_deg + i * (360 / chambers))
        cx = _ART_CENTER + _ART_CYL_RADIUS * 0.55 * math.cos(angle)
        cy = _ART_CENTER + _ART_CYL_RADIUS * 0.55 * math.sin(angle)

        is_loaded = loaded_index is not None and i == loaded_index
        fill = _ART_CHAMBER_LOADED if (is_loaded and highlight) else _ART_CHAMBER_EMPTY

        draw.ellipse(
            [cx - _ART_CHAMBER_RADIUS, cy - _ART_CHAMBER_RADIUS, cx + _ART_CHAMBER_RADIUS, cy + _ART_CHAMBER_RADIUS],
            fill=fill,
            outline=_ART_OUTLINE,
            width=3,
        )
        # small highlight for depth
        draw.ellipse(
            [
                cx - _ART_CHAMBER_RADIUS + 4,
                cy - _ART_CHAMBER_RADIUS + 4,
                cx - _ART_CHAMBER_RADIUS + 12,
                cy - _ART_CHAMBER_RADIUS + 12,
            ],
            fill=(255, 255, 255, 60),
        )

    # center pin
    draw.ellipse(
        [_ART_CENTER - 14, _ART_CENTER - 14, _ART_CENTER + 14, _ART_CENTER + 14],
        fill=_ART_METAL_LIGHT,
        outline=_ART_OUTLINE,
        width=2,
    )

    return img


def build_spin_gif(chambers: int, *, total_frames: int = 24) -> io.BytesIO:
    """
    Spinning cylinder GIF that eases to a stop. Loaded chamber is never
    revealed here (highlight=False) — this is only the suspense phase.
    Returns an in-memory BytesIO ready to attach to a discord.File.
    """
    frames = []
    total_rotation = 360 * 3 + 40  # ~3 full spins plus an offset

    for f in range(total_frames):
        t = f / (total_frames - 1)
        eased = 1 - (1 - t) ** 3  # ease-out cubic: fast start, slow finish
        rot = eased * total_rotation
        frames.append(_draw_cylinder(rot, chambers))

    # slow durations down towards the end frames for a "settling" feel
    fast = [60] * (total_frames - 8)
    slow = [90, 110, 130, 160, 200, 260, 320, 400]
    durations = (fast + slow)[:total_frames]

    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=1,
        disposal=2,
    )
    buf.seek(0)
    return buf


def build_result_image(chambers: int, loaded_index: int, hit: bool) -> io.BytesIO:
    """
    Static reveal frame. If hit is True, the loaded chamber is shown in red.
    If hit is False (survived), the cylinder shows as all-clear.
    """
    img = _draw_cylinder(0, chambers, loaded_index=loaded_index, highlight=hit)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


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

        # Resolve the outcome up front so the image generation matches the result
        loaded_index = random.randrange(ROULETTE_CHAMBERS)
        chamber = random.randrange(ROULETTE_CHAMBERS)
        hit = chamber == loaded_index

        # --- Spin phase ---
        # Send the real message immediately (no defer -> no "Bot is thinking..." bubble)
        spin_gif = build_spin_gif(ROULETTE_CHAMBERS)
        spin_file = discord.File(fp=spin_gif, filename="spin.gif")

        spin_embed = discord.Embed(
            title="🔫 Spinning the cylinder...",
            description=f"{member.mention} points it at their own head and pulls the trigger.",
            color=ROULETTE_COLOR_SPIN,
        )
        spin_embed.set_image(url="attachment://spin.gif")
        spin_embed.set_footer(text=f"Chambers: {ROULETTE_CHAMBERS} • 1 is loaded")

        await interaction.response.send_message(embed=spin_embed, file=spin_file)
        msg = await interaction.original_response()

        await asyncio.sleep(ROULETTE_SPIN_WAIT)

        # --- Result phase ---
        result_img = build_result_image(ROULETTE_CHAMBERS, loaded_index, hit)
        result_file = discord.File(fp=result_img, filename="result.png")

        if hit:
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

        result_embed.set_image(url="attachment://result.png")
        result_embed.set_footer(text=f"Chamber {chamber + 1} of {ROULETTE_CHAMBERS}")

        # Edit in the new attachment (need attachments= to swap the image file on an edit)
        await msg.edit(embed=result_embed, attachments=[result_file])

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