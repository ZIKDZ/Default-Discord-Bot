import json
import logging
from pathlib import Path
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

TZ = ZoneInfo("Africa/Algiers")
ALADHAN_METHOD_ALGERIA = 19  # Algeria method on AlAdhan

# Default wilaya if user never set one
DEFAULT_WILAYA = 16  # Alger


# Data storage
def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


DATA_DIR = _project_root() / "data"
DATA_FILE = DATA_DIR / "maghrib_prefs.json"


def _load_prefs() -> dict:
    if not DATA_FILE.exists():
        return {}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        log.exception("Failed to read maghrib_prefs.json; using empty prefs.")
        return {}


def _save_prefs(prefs: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(prefs, indent=2, ensure_ascii=False), encoding="utf-8")


# Wilaya code -> capital city (58 codes)
WILAYA_CITY = {
    1: "Adrar",
    2: "Chlef",
    3: "Laghouat",
    4: "Oum El Bouaghi",
    5: "Batna",
    6: "Bejaia",
    7: "Biskra",
    8: "Bechar",
    9: "Blida",
    10: "Bouira",
    11: "Tamanrasset",
    12: "Tebessa",
    13: "Tlemcen",
    14: "Tiaret",
    15: "Tizi Ouzou",
    16: "Algiers",
    17: "Djelfa",
    18: "Jijel",
    19: "Setif",
    20: "Saida",
    21: "Skikda",
    22: "Sidi Bel Abbes",
    23: "Annaba",
    24: "Guelma",
    25: "Constantine",
    26: "Medea",
    27: "Mostaganem",
    28: "M'Sila",
    29: "Mascara",
    30: "Ouargla",
    31: "Oran",
    32: "El Bayadh",
    33: "Illizi",
    34: "Bordj Bou Arreridj",
    35: "Boumerdes",
    36: "El Tarf",
    37: "Tindouf",
    38: "Tissemsilt",
    39: "El Oued",
    40: "Khenchela",
    41: "Souk Ahras",
    42: "Tipaza",
    43: "Mila",
    44: "Ain Defla",
    45: "Naama",
    46: "Ain Temouchent",
    47: "Ghardaia",
    48: "Relizane",
    49: "Timimoun",
    50: "Bordj Badji Mokhtar",
    51: "Ouled Djellal",
    52: "Beni Abbes",
    53: "In Salah",
    54: "In Guezzam",
    55: "Touggourt",
    56: "Djanet",
    57: "El M'Ghair",
    58: "El Menia",
}


def _fmt_delta(seconds: int) -> str:
    if seconds < 0:
        seconds = -seconds
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}h {m}m {s}s"
    return f"{m}m {s}s"


def _parse_hhmm(hhmm: str, for_day: date) -> datetime:
    # API usually returns "HH:MM"
    hh, mm = hhmm.strip().split(":")
    return datetime(for_day.year, for_day.month, for_day.day, int(hh), int(mm), tzinfo=TZ)


async def _get_maghrib_time(city: str, on_date: date) -> tuple[datetime, str]:
    """
    Returns (maghrib_datetime, readable_hhmm) for given city/date in Algeria.
    Uses AlAdhan timingsByCity endpoint.
    """
    d = on_date.strftime("%d-%m-%Y")
    url = f"https://api.aladhan.com/v1/timingsByCity/{d}"
    params = {
        "city": city,
        "country": "Algeria",
        "method": str(ALADHAN_METHOD_ALGERIA),
    }

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=params) as resp:
            data = await resp.json(content_type=None)

    if not isinstance(data, dict) or data.get("code") != 200:
        raise RuntimeError(f"AlAdhan API error: {data.get('status') if isinstance(data, dict) else 'unknown'}")

    timings = data["data"]["timings"]
    maghrib_str = timings["Maghrib"]  # e.g. "18:25"
    maghrib_dt = _parse_hhmm(maghrib_str, on_date)
    return maghrib_dt, maghrib_str


class MaghribCountdown(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _get_user_wilaya(self, user_id: int) -> int:
        prefs = _load_prefs()
        return int(prefs.get(str(user_id), DEFAULT_WILAYA))

    def _set_user_wilaya(self, user_id: int, wilaya: int) -> None:
        prefs = _load_prefs()
        prefs[str(user_id)] = wilaya
        _save_prefs(prefs)

    @app_commands.command(name="ftor_set", description="Set your default wilaya number (1-58) for /ftor")
    @app_commands.describe(wilaya="Wilaya number (e.g., 16 for Algiers, 31 for Oran)")
    async def set_default(self, interaction: discord.Interaction, wilaya: int):
        if wilaya not in WILAYA_CITY:
            return await interaction.response.send_message(
                "❌ Invalid wilaya. Use a number from **1 to 58**.",
                ephemeral=True,
            )

        self._set_user_wilaya(interaction.user.id, wilaya)
        await interaction.response.send_message(
            f"✅ Saved your default wilaya: **{wilaya} – {WILAYA_CITY[wilaya]}**",
            ephemeral=True,
        )

    @app_commands.command(name="ftor", description="Time left until Maghrib (Iftar)")
    @app_commands.describe(wilaya="Optional wilaya number (1-58). If omitted, uses your saved/default wilaya.")
    async def timeleft(self, interaction: discord.Interaction, wilaya: int | None = None):
        w = wilaya if wilaya is not None else self._get_user_wilaya(interaction.user.id)

        if w not in WILAYA_CITY:
            return await interaction.response.send_message(
                "❌ Invalid wilaya. Use a number from **1 to 58**.",
                ephemeral=True,
            )

        city = WILAYA_CITY[w]
        now = datetime.now(TZ)
        today = now.date()

        await interaction.response.defer()

        try:
            maghrib_dt, maghrib_str = await _get_maghrib_time(city, today)

            if now >= maghrib_dt:
                tomorrow = today + timedelta(days=1)
                maghrib_dt, maghrib_str = await _get_maghrib_time(city, tomorrow)

            left_seconds = int((maghrib_dt - now).total_seconds())

            embed = discord.Embed(
                title=f"Iftar — {city}",
                description="Sunset marks the time of Iftar.",
                color=10181046,
            )

            embed.add_field(
                name="🌙 Adhan",
                value=f"`{maghrib_str} (Local Time)`",
                inline=True,
            )

            embed.add_field(
                name="⏳ Countdown",
                value=f"`{_fmt_delta(left_seconds)}`",
                inline=True,
            )

            embed.set_footer(text=f"Wilaya {w} • Iftar Notification")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            log.exception("Maghrib lookup failed")
            await interaction.followup.send(
                f"❌ Failed to fetch Maghrib time: `{type(e).__name__}: {e}`"
            )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandInvokeError) and error.original:
            # If response already sent, use followup safely
            if interaction.response.is_done():
                return await interaction.followup.send(f"❌ Error: `{error.original}`", ephemeral=True)
            return await interaction.response.send_message(f"❌ Error: `{error.original}`", ephemeral=True)
        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(MaghribCountdown(bot))