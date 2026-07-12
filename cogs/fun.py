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
# Design notes (why it looks the way it does):
#  - A fixed red "hammer" marker sits at 12 o'clock on every frame — this is
#    the firing position. Whichever chamber is rotated to meet it is the one
#    that fires.
#  - The loaded chamber only ever gets a visible bullet drawn in it in the
#    FINAL hit frame, rotated so it sits exactly under the hammer, plus a
#    muzzle flash. That's what makes a hit unambiguous.
#  - On a miss, the loaded chamber is rotated away from the hammer and never
#    drawn with a bullet, so nothing spoils which chamber was loaded.

_ART_SIZE = 320
_ART_CENTER = _ART_SIZE // 2
_ART_CYL_RADIUS = 95
_ART_CHAMBER_RADIUS = 26

_ART_BG = (0, 0, 0, 0)  # transparent
_ART_METAL = (100, 104, 112, 255)
_ART_METAL_LIGHT = (160, 164, 172, 255)
_ART_METAL_DARK = (60, 63, 69, 255)
_ART_CHAMBER_EMPTY = (25, 26, 29, 255)
_ART_OUTLINE = (15, 16, 18, 255)
_ART_BRASS = (196, 155, 71, 255)
_ART_BULLET_TIP = (140, 143, 148, 255)
_ART_FLASH_YELLOW = (255, 214, 90, 255)
_ART_FLASH_ORANGE = (255, 130, 40, 255)
_ART_HAMMER_COLOR = (210, 60, 60, 255)


def _chamber_pos(rotation_deg: float, i: int, chambers: int) -> tuple[float, float]:
    angle = math.radians(rotation_deg + i * (360 / chambers))
    cx = _ART_CENTER + _ART_CYL_RADIUS * 0.55 * math.cos(angle)
    cy = _ART_CENTER + _ART_CYL_RADIUS * 0.55 * math.sin(angle)
    return cx, cy


def _draw_bullet(draw: ImageDraw.ImageDraw, cx: float, cy: float) -> None:
    """A bullet casing viewed from above, sitting inside a chamber."""
    r = _ART_CHAMBER_RADIUS - 5
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_ART_BRASS, outline=_ART_OUTLINE, width=2)
    tip_r = r * 0.5
    draw.ellipse(
        [cx - tip_r, cy - tip_r, cx + tip_r, cy + tip_r],
        fill=_ART_BULLET_TIP,
        outline=_ART_OUTLINE,
        width=1,
    )
    draw.ellipse([cx - r + 3, cy - r + 3, cx - r + 9, cy - r + 9], fill=(255, 255, 255, 90))


def _draw_cylinder(
    rotation_deg: float,
    chambers: int,
    loaded_index: int | None = None,
    reveal: bool = False,
) -> tuple[Image.Image, tuple[float, float]]:
    """
    Draws the cylinder plus a fixed hammer marker at 12 o'clock.
    Returns (image, hammer_xy) — hammer_xy is used to place the muzzle flash.
    """
    img = Image.new("RGBA", (_ART_SIZE, _ART_SIZE), _ART_BG)
    draw = ImageDraw.Draw(img)

    hammer_x, hammer_y = _ART_CENTER, _ART_CENTER - _ART_CYL_RADIUS - 34

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
        cx, cy = _chamber_pos(rotation_deg, i, chambers)
        is_loaded = loaded_index is not None and i == loaded_index

        draw.ellipse(
            [cx - _ART_CHAMBER_RADIUS, cy - _ART_CHAMBER_RADIUS, cx + _ART_CHAMBER_RADIUS, cy + _ART_CHAMBER_RADIUS],
            fill=_ART_CHAMBER_EMPTY,
            outline=_ART_OUTLINE,
            width=3,
        )

        if is_loaded and reveal:
            _draw_bullet(draw, cx, cy)
        else:
            draw.ellipse(
                [cx - _ART_CHAMBER_RADIUS + 4, cy - _ART_CHAMBER_RADIUS + 4, cx - _ART_CHAMBER_RADIUS + 11, cy - _ART_CHAMBER_RADIUS + 11],
                fill=(255, 255, 255, 45),
            )

    # center pin
    draw.ellipse(
        [_ART_CENTER - 14, _ART_CENTER - 14, _ART_CENTER + 14, _ART_CENTER + 14],
        fill=_ART_METAL_LIGHT,
        outline=_ART_OUTLINE,
        width=2,
    )

    # fixed hammer / firing-pin marker — this is the reference point that
    # tells you which chamber is "up next"
    draw.polygon(
        [
            (hammer_x - 12, hammer_y - 14),
            (hammer_x + 12, hammer_y - 14),
            (hammer_x, hammer_y + 10),
        ],
        fill=_ART_HAMMER_COLOR,
        outline=_ART_OUTLINE,
    )
    draw.rectangle(
        [hammer_x - 4, hammer_y - 24, hammer_x + 4, hammer_y - 12],
        fill=_ART_METAL_DARK,
        outline=_ART_OUTLINE,
    )

    return img, (hammer_x, hammer_y)


def _draw_muzzle_flash(img: Image.Image, cx: float, cy: float) -> Image.Image:
    draw = ImageDraw.Draw(img)
    rng = random.Random(1234)  # fixed seed so the flash shape looks intentional, not glitchy
    for i in range(10):
        ang = math.radians(i * 36 + rng.uniform(-8, 8))
        length = rng.uniform(35, 60)
        x2 = cx + length * math.cos(ang)
        y2 = cy + length * math.sin(ang) - 40
        draw.line([(cx, cy - 40), (x2, y2)], fill=_ART_FLASH_ORANGE, width=6)
    draw.ellipse([cx - 26, cy - 66, cx + 26, cy - 14], fill=_ART_FLASH_YELLOW)
    draw.ellipse([cx - 14, cy - 58, cx + 14, cy - 22], fill=(255, 255, 255, 230))
    return img


def build_spin_gif(chambers: int, *, total_frames: int = 24) -> io.BytesIO:
    """
    Spinning cylinder GIF that eases to a stop. The bullet is never drawn
    during the spin (reveal=False everywhere) — this is only the suspense
    phase, so nothing gives away the outcome early.
    """
    frames = []
    total_rotation = 360 * 3 + 40  # ~3 full spins plus an offset

    for f in range(total_frames):
        t = f / (total_frames - 1)
        eased = 1 - (1 - t) ** 3  # ease-out cubic: fast start, slow finish
        rot = eased * total_rotation
        im, _ = _draw_cylinder(rot, chambers)
        frames.append(im)

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


def build_result_image(chambers: int, loaded_index: int, landing_index: int, hit: bool) -> io.BytesIO:
    """
    Static reveal frame, rotated so `landing_index` (the chamber that fired)
    sits exactly under the fixed hammer marker at the top.

    - hit=True:  landing_index == loaded_index. The bullet is drawn in that
      chamber, right under the hammer, plus a muzzle flash. Unambiguous.
    - hit=False: landing_index != loaded_index. The chamber under the hammer
      is empty; the actual loaded chamber sits elsewhere on the wheel and is
      never revealed, so it doesn't spoil where the bullet was.
    """
    # Rotate the wheel so `landing_index` lands at the top (-90deg / 12 o'clock)
    step = 360 / chambers
    rotation = (-90 - landing_index * step) % 360

    img, (hx, hy) = _draw_cylinder(rotation, chambers, loaded_index=loaded_index, reveal=hit)
    if hit:
        img = _draw_muzzle_flash(img, hx, hy)

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

        # Resolve the outcome up front so the image generation matches the result.
        # loaded_index  = which chamber has the bullet.
        # landing_index = which chamber the cylinder actually stops on (under the hammer).
        loaded_index = random.randrange(ROULETTE_CHAMBERS)
        landing_index = random.randrange(ROULETTE_CHAMBERS)
        hit = landing_index == loaded_index

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
        result_img = build_result_image(ROULETTE_CHAMBERS, loaded_index, landing_index, hit)
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
        result_embed.set_footer(text=f"Chamber {landing_index + 1} of {ROULETTE_CHAMBERS}")

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