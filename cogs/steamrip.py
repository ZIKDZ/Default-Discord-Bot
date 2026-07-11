import logging
import re
import json
import random
import io
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlparse, quote_plus

import aiohttp
import discord
from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
STEAMRIP_BASE = "https://steamrip.com/"


# ================================
#   Shared Helpers
# ================================

def soft_error_embed(title: str, message: str) -> discord.Embed:
    return discord.Embed(
        title=f"✨ {title}",
        description=message,
        color=0xF2B705,
        timestamp=datetime.utcnow(),
    )


def normalize_url(href: str, base_url: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith("//"):
        href = "https:" + href
    href = urljoin(base_url, href)
    parsed = urlparse(href)
    if parsed.scheme not in ("http", "https"):
        return None
    return href


def clean_game_title(title: str) -> str:
    if not title:
        return "Unknown Game"
    title = re.sub(r"\s*free\s*download\s*", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\s{2,}", " ", title).strip()
    return title


def strip_version_suffix(title: str) -> str:
    """Removes trailing "(...)" suffix ONLY for matching."""
    if not title:
        return title
    return re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()


def match_key(text: str) -> str:
    """Comparison key for exact-ish matching (case-insensitive, no punctuation)."""
    if not text:
        return ""
    text = text.casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


async def fetch_page(url: str) -> BeautifulSoup:
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {"User-Agent": UA}

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError("page_unavailable")
            html = await resp.text()
            return BeautifulSoup(html, "html.parser")


async def fetch_image_as_discord_file(
    image_url: str,
    *,
    max_bytes: int = 8 * 1024 * 1024,
) -> discord.File | None:
    if not image_url:
        return None

    timeout = aiohttp.ClientTimeout(total=20)
    headers = {"User-Agent": UA}

    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(image_url) as resp:
                if resp.status != 200:
                    return None
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if "image" not in ctype:
                    return None
                data = await resp.read()
                if not data or len(data) > max_bytes:
                    return None
                ext = "png"
                if "jpeg" in ctype or "jpg" in ctype:
                    ext = "jpg"
                elif "webp" in ctype:
                    ext = "webp"
                elif "gif" in ctype:
                    ext = "gif"
                filename = f"steamrip_cover.{ext}"
                return discord.File(fp=io.BytesIO(data), filename=filename)
    except Exception:
        return None


def host_to_label(netloc: str) -> str:
    host = (netloc or "").lower().replace("www.", "").strip()
    if not host:
        return "Download"
    name = host.split(".")[0].replace("-", " ").replace("_", " ").title()
    name = name.replace("Db", "DB").replace("Io", "IO")
    return name[:80] or "Download"


# ================================
#   Adaptive Search Parsing
# ================================

@dataclass
class SearchResult:
    title: str
    url: str
    image: str | None = None


def parse_search_results(soup: BeautifulSoup) -> list[SearchResult]:
    results: list[SearchResult] = []
    
    # Adaptive container search if 'masonry-grid' has layout modifications
    grid = (
        soup.find("div", id="masonry-grid") 
        or soup.find("main") 
        or soup.find("div", class_=lambda c: c and any(w in c.lower() for w in ["grid", "content", "main-wrapper"]))
        or soup
    )

    seen_urls: set[str] = set()

    # Search for specific post wrappers, structural semantic tags, or grid containers
    cards = grid.select("div.post-element") or grid.select("article") or grid.select(".container-wrapper")
    
    # Safety fallback if container elements were abstracted entirely
    if not cards:
        cards = [a for a in grid.find_all("a", href=True) if "/wp-content/uploads/" not in a["href"] and len(a.get_text(strip=True)) > 3]

    for card in cards:
        a_tag = (
            card if card.name == "a" 
            else (card.select_one("a.all-over-thumb-link") or card.select_one("h2 a") or card.select_one("h3 a") or card.find("a", href=True))
        )
        if not a_tag:
            continue

        raw_href = a_tag.get("href") or ""
        href = normalize_url(raw_href, STEAMRIP_BASE)
        if not href or href in seen_urls or href == STEAMRIP_BASE:
            continue

        # Extract title from multi-tiered contextual positions
        title_el = card.select_one("h2.thumb-title a") or card.select_one("h2") or card.select_one("h3") or a_tag
        title = title_el.get_text(strip=True) if title_el else ""
        
        sr = card.select_one("span.screen-reader-text")
        if sr and sr.get_text(strip=True) in title:
            title = title.replace(sr.get_text(strip=True), "").strip()

        title = clean_game_title(title)
        if not title or len(title) < 2:
            continue

        # Extract imagery securely from responsive backgrounds or native image properties
        img = None
        slide = card.select_one("div.slide") or card.select_one(".thumb-image")
        if slide:
            img = slide.get("data-back") or slide.get("data-back-webp") or slide.get("style")
            if img and "url(" in str(img):
                match = re.search(r"url\(['\"]?([^'\")]+)['\"]?\)", str(img))
                if match:
                    img = match.group(1)
        
        if not img:
            img_tag = card.find("img")
            if img_tag:
                img = img_tag.get("data-src") or img_tag.get("src")

        if img:
            img = normalize_url(str(img), STEAMRIP_BASE)

        results.append(SearchResult(title=title, url=href, image=img))
        seen_urls.add(href)

    return results


# ================================
#   Adaptive Game Page Parsing
# ================================

def extract_game_data(soup: BeautifulSoup, url: str) -> dict:
    scripts = soup.find_all("script", type="application/ld+json")
    schema = None
    for script in scripts:
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and data.get("@type") in ("BlogPosting", "Article"):
                schema = data
                break
        except Exception:
            continue

    title = schema.get("headline", "") if schema else ""
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else "Unknown Title"
        
    title = clean_game_title(title)

    image = None
    if schema:
        image = (
            (schema.get("image") or {}).get("url")
            or (schema.get("image") or {}).get("@id")
            or schema.get("image")
        )
    if not image:
        img_el = soup.find("meta", property="og:image")
        image = img_el.get("content") if img_el else None

    date_modified = schema.get("dateModified", "") if schema else ""
    if not date_modified:
        meta_date = soup.find("meta", property="article:modified_time")
        date_modified = meta_date.get("content", "") if meta_date else ""

    game_info: dict[str, str] = {}
    system_reqs: list[str] = []

    content = soup.find("div", class_="entry-content") or soup.find("article")
    if content:
        for li in content.find_all("li"):
            text = li.get_text(strip=True)
            if "genre" in text.lower():
                game_info["Genre"] = text.split(":", 1)[-1].strip()
            elif "developer" in text.lower():
                game_info["Developer"] = text.split(":", 1)[-1].strip()
            elif "platform" in text.lower():
                game_info["Platform"] = text.split(":", 1)[-1].strip()
            elif "game size" in text.lower():
                game_info["Game Size"] = text.split(":", 1)[-1].strip()
            elif "version" in text.lower():
                game_info["Version"] = text.split(":", 1)[-1].strip()
            elif "pre-installed" in text.lower():
                game_info["Pre-Installed"] = "Yes"

        sys_section = content.find(
            lambda tag: tag.name in ["h3", "h4", "div", "strong"] 
            and tag.text and "SYSTEM REQUIREMENTS" in tag.text.upper()
        )
        if sys_section:
            ul = sys_section.find_next("ul")
            if ul:
                for li in ul.find_all("li"):
                    txt = li.get_text(strip=True)
                    if txt:
                        system_reqs.append("• " + txt)

    fields = []
    for key in ["Genre", "Developer", "Platform", "Game Size", "Version", "Pre-Installed"]:
        if key in game_info:
            fields.append({"name": key, "value": game_info[key][:1024], "inline": True})

    if system_reqs:
        fields.append(
            {"name": "System Requirements", "value": "\n".join(system_reqs)[:1024], "inline": False}
        )

    return {
        "title": title,
        "url": url,
        "image": image,
        "date_modified": date_modified,
        "fields": fields,
    }


def extract_download_links(soup: BeautifulSoup, page_url: str) -> list[dict]:
    links = []
    content = soup.find("div", class_="entry-content") or soup.find("article") or soup
    
    for a in content.find_all("a", href=True):
        href = normalize_url(a.get("href") or "", page_url)
        if not href:
            continue
            
        netloc = urlparse(href).netloc.lower()
        if not netloc or "steamrip.com" in netloc or any(x in href for x in ["facebook", "twitter", "pinterest", "wp-content"]):
            continue
            
        classes = " ".join(a.get("class", [])).lower()
        is_button = any(w in classes for w in ["button", "btn", "shortc"])
        is_filehost = any(host in netloc for host in ["mega", "gofile", "qiwi", "pixeldrain", "buzzheavier", "krakenfiles", "1fichier", "torrent"])

        if is_button or is_filehost:
            label = host_to_label(netloc)
            link_text = a.get_text(strip=True)
            if link_text and len(link_text) < 30 and "download" not in link_text.lower():
                label = f"{label} ({link_text})"
                
            links.append({"label": label, "url": href})
            
    seen = set()
    deduped_links = []
    for l in links:
        if l["url"] not in seen:
            seen.add(l["url"])
            deduped_links.append(l)

    return deduped_links


# ================================
#   Game Layout Presentation
# ================================

async def create_game_embed_and_view(
    interaction: discord.Interaction,
    game_url: str,
    *,
    ephemeral: bool = False,
):
    try:
        soup = await fetch_page(game_url)
        data = extract_game_data(soup, game_url)
        dl_links = extract_download_links(soup, game_url)

        embed = discord.Embed(
            title=data["title"][:256],
            url=data["url"],
            color=0xFFFFFF,
            timestamp=datetime.utcnow(),
        )

        files = []
        if data["image"]:
            img_url = normalize_url(str(data["image"]), game_url) or str(data["image"])
            cover = await fetch_image_as_discord_file(img_url)
            if cover:
                files.append(cover)
                embed.set_image(url=f"attachment://{cover.filename}")

        embed.set_footer(
            text=f"SteamRIP • Updated {data['date_modified'][:10] or 'unknown'}",
            icon_url="https://steamrip.com/wp-content/uploads/2021/06/cropped-favicon1-192x192.png",
        )

        for f in data["fields"]:
            embed.add_field(
                name=f["name"],
                value=f["value"] or "—",
                inline=f.get("inline", False),
            )

        view = discord.ui.View()
        for link in dl_links[:5]:
            view.add_item(
                discord.ui.Button(
                    label=link["label"][:80],
                    url=link["url"],
                    style=discord.ButtonStyle.url,
                    row=0,
                )
            )

        if ephemeral:
            await interaction.followup.send(embed=embed, view=view, files=files, ephemeral=True)
        else:
            await interaction.channel.send(embed=embed, view=view, files=files)

    except Exception:
        log.exception("Failed to create game embed")
        embed = soft_error_embed(
            "Couldn't load game",
            "Something went wrong while fetching that game page.",
        )
        if ephemeral:
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.channel.send(embed=embed)


# ================================
#   Button UI for Search Interfaces
# ================================

def random_id() -> int:
    return random.randint(100_000_000, 999_999_999)


class PostGameButton(discord.ui.Button):
    def __init__(self, game_url: str):
        super().__init__(
            label="Post Game Links",
            style=discord.ButtonStyle.green,
            custom_id=f"post_game_{random_id()}",
        )
        self.game_url = game_url

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await create_game_embed_and_view(interaction, self.game_url, ephemeral=False)
        await interaction.followup.send("Game posted above ↑", ephemeral=True)


# ================================
#   Main Cog Command Controller
# ================================

class SteamRIP(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="game", description="Search SteamRIP games")
    @app_commands.describe(query="Game name (e.g. cyberpunk 2077)")
    async def game_search(self, interaction: discord.Interaction, query: str):
        query = query.strip()
        if not query:
            return await interaction.response.send_message(
                embed=soft_error_embed("Missing search", "Please enter a game name."),
                ephemeral=True,
            )

        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            search_url = f"{STEAMRIP_BASE}?s={quote_plus(query)}"
            soup = await fetch_page(search_url)

            all_results = parse_search_results(soup)
            q_key = match_key(query)

            perfect = None
            for r in all_results:
                t1 = match_key(r.title)
                t2 = match_key(strip_version_suffix(r.title))
                if q_key and (q_key == t1 or q_key == t2):
                    perfect = r
                    break

            results = [perfect] if perfect else all_results[:4]

            if not results:
                embed = soft_error_embed(
                    "No results",
                    f"Nothing found for **{query}**.\nTry different spelling or shorter name.",
                )
                return await interaction.followup.send(embed=embed, ephemeral=True)

            for idx, res in enumerate(results, 1):
                embed = discord.Embed(
                    title=f"{idx}. {res.title}"[:256],
                    url=res.url,
                    color=0xFFFFFF,
                    timestamp=datetime.utcnow(),
                )

                cover = None
                if res.image:
                    cover = await fetch_image_as_discord_file(res.image)
                    if cover:
                        embed.set_image(url=f"attachment://{cover.filename}")

                embed.set_footer(text='SteamRIP • Click "Post Game Links" to share')

                view = discord.ui.View(timeout=180)
                view.add_item(PostGameButton(res.url))

                await interaction.followup.send(
                    embed=embed,
                    view=view,
                    files=[cover] if cover else None,
                    ephemeral=True,
                )

        except Exception:
            log.exception("Search failed")
            embed = soft_error_embed("Search failed", "Couldn't reach SteamRIP right now.")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="post", description="Post a formatted SteamRIP game embed")
    @app_commands.describe(url="Full SteamRIP game page URL")
    async def post_game(self, interaction: discord.Interaction, url: str):
        url = url.strip()
        if not url.startswith("https://steamrip.com/"):
            embed = soft_error_embed(
                "Invalid link",
                "Please use a link starting with `https://steamrip.com/`",
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.defer(thinking=True)

        try:
            await create_game_embed_and_view(interaction, url, ephemeral=False)
            await interaction.followup.send("Posted!", ephemeral=True)
        except Exception:
            embed = soft_error_embed("Couldn't post", "Failed to load that game page.")
            await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SteamRIP(bot))