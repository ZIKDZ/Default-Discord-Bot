import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase import create_client, Client

from core.config import Config

log = logging.getLogger(__name__)

_client: Optional[Client] = None
_warned = False

WARNING_EXPIRY_DAYS = 7


def get_client() -> Optional[Client]:
    """Lazily create and cache the Supabase client. Returns None if not configured."""
    global _client, _warned

    if _client is not None:
        return _client

    cfg = Config()
    url = cfg.get("SUPABASE_URL")
    key = cfg.get("SUPABASE_KEY")

    if not url or not key:
        if not _warned:
            log.warning(
                "Supabase not configured (SUPABASE_URL / SUPABASE_KEY missing in .env). "
                "DM logging, presets, and automod warnings will be disabled."
            )
            _warned = True
        return None

    try:
        _client = create_client(url, key)
        return _client
    except Exception:
        log.exception("Failed to create Supabase client")
        return None


async def _run(fn):
    """Run a blocking supabase-py call off the event loop."""
    return await asyncio.to_thread(fn)


# ================================
#   DM logs
# ================================

async def log_dm(
    *,
    guild_id: int | None,
    sender_id: int,
    target_id: int,
    kind: str,  # "plain" | "embed" | "preset"
    preset_name: str | None,
    content: str | None,
    embed_json: dict | None,
    success: bool,
    error: str | None = None,
) -> None:
    client = get_client()
    if not client:
        return

    payload = {
        "guild_id": str(guild_id) if guild_id else None,
        "sender_id": str(sender_id),
        "target_id": str(target_id),
        "kind": kind,
        "preset_name": preset_name,
        "content": content,
        "embed_json": embed_json,
        "success": success,
        "error": error,
    }

    try:
        await _run(lambda: client.table("dm_logs").insert(payload).execute())
    except Exception:
        log.exception("Failed to log DM to Supabase")


# ================================
#   Presets
# ================================

async def create_preset(
    *,
    guild_id: int,
    name: str,
    title: str | None,
    description: str | None,
    color: int | None,
    image_url: str | None,
    footer: str | None,
    created_by: int,
) -> bool:
    client = get_client()
    if not client:
        return False

    payload = {
        "guild_id": str(guild_id),
        "name": name.strip().lower(),
        "title": title,
        "description": description,
        "color": color,
        "image_url": image_url,
        "footer": footer,
        "created_by": str(created_by),
    }

    try:
        await _run(
            lambda: client.table("dm_presets")
            .upsert(payload, on_conflict="guild_id,name")
            .execute()
        )
        return True
    except Exception:
        log.exception("Failed to create/update preset")
        return False


async def get_preset(*, guild_id: int, name: str) -> dict | None:
    client = get_client()
    if not client:
        return None

    try:
        res = await _run(
            lambda: client.table("dm_presets")
            .select("*")
            .eq("guild_id", str(guild_id))
            .eq("name", name.strip().lower())
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None
    except Exception:
        log.exception("Failed to fetch preset")
        return None


async def list_presets(*, guild_id: int) -> list[dict]:
    client = get_client()
    if not client:
        return []

    try:
        res = await _run(
            lambda: client.table("dm_presets")
            .select("name")
            .eq("guild_id", str(guild_id))
            .order("name")
            .execute()
        )
        return res.data or []
    except Exception:
        log.exception("Failed to list presets")
        return []


async def delete_preset(*, guild_id: int, name: str) -> bool:
    client = get_client()
    if not client:
        return False

    try:
        await _run(
            lambda: client.table("dm_presets")
            .delete()
            .eq("guild_id", str(guild_id))
            .eq("name", name.strip().lower())
            .execute()
        )
        return True
    except Exception:
        log.exception("Failed to delete preset")
        return False


# ================================
#   AutoMod warnings
# ================================
#
# A user's "active" warning count = warnings issued in the last
# WARNING_EXPIRY_DAYS days that haven't been manually cleared. Each warning
# ages out independently WARNING_EXPIRY_DAYS after it was given — that's what
# gives the "no warnings for a week -> level drops" behavior, with no
# background job needed to decrement anything.

async def add_warning(
    *,
    guild_id: int,
    target_id: int,
    moderator_id: int,
    reason: str | None,
) -> bool:
    client = get_client()
    if not client:
        return False

    payload = {
        "guild_id": str(guild_id),
        "target_id": str(target_id),
        "moderator_id": str(moderator_id),
        "reason": reason,
    }

    try:
        await _run(lambda: client.table("automod_warnings").insert(payload).execute())
        return True
    except Exception:
        log.exception("Failed to insert warning")
        return False


async def get_active_warnings(*, guild_id: int, target_id: int) -> list[dict]:
    """
    Returns warnings for this user that are still 'active': not manually
    cleared, and issued within the last WARNING_EXPIRY_DAYS days.
    Ordered oldest -> newest.
    """
    client = get_client()
    if not client:
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=WARNING_EXPIRY_DAYS)).isoformat()

    try:
        res = await _run(
            lambda: client.table("automod_warnings")
            .select("*")
            .eq("guild_id", str(guild_id))
            .eq("target_id", str(target_id))
            .eq("cleared", False)
            .gte("created_at", cutoff)
            .order("created_at")
            .execute()
        )
        return res.data or []
    except Exception:
        log.exception("Failed to fetch active warnings")
        return []


async def get_all_warnings(*, guild_id: int, target_id: int) -> list[dict]:
    """Full warning history for a user (including expired/cleared), newest first."""
    client = get_client()
    if not client:
        return []

    try:
        res = await _run(
            lambda: client.table("automod_warnings")
            .select("*")
            .eq("guild_id", str(guild_id))
            .eq("target_id", str(target_id))
            .order("created_at", desc=True)
            .execute()
        )
        return res.data or []
    except Exception:
        log.exception("Failed to fetch warning history")
        return []


async def clear_warnings(*, guild_id: int, target_id: int, cleared_by: int) -> int:
    """Marks all currently-active warnings as cleared. Returns how many were cleared."""
    client = get_client()
    if not client:
        return 0

    active = await get_active_warnings(guild_id=guild_id, target_id=target_id)
    if not active:
        return 0

    ids = [row["id"] for row in active]

    try:
        await _run(
            lambda: client.table("automod_warnings")
            .update(
                {
                    "cleared": True,
                    "cleared_by": str(cleared_by),
                    "cleared_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .in_("id", ids)
            .execute()
        )
        return len(ids)
    except Exception:
        log.exception("Failed to clear warnings")
        return 0

# ================================================================
#   VoiceMaster (temp voice channels)
#   -> Append this block to core/supabase_client.py
#      (it reuses get_client() / _run() already defined above)
# ================================================================

# ── Hub config ───────────────────────────────────────────────────

async def set_hub(
    *,
    guild_id: int,
    join_channel_id: int,
    category_id: int | None,
    panel_channel_id: int | None,
    name_template: str,
    default_limit: int,
    created_by: int,
) -> bool:
    client = get_client()
    if not client:
        return False

    payload = {
        "guild_id": str(guild_id),
        "join_channel_id": str(join_channel_id),
        "category_id": str(category_id) if category_id else None,
        "panel_channel_id": str(panel_channel_id) if panel_channel_id else None,
        "name_template": name_template,
        "default_limit": default_limit,
        "created_by": str(created_by),
    }

    try:
        await _run(
            lambda: client.table("voicemaster_hubs")
            .upsert(payload, on_conflict="guild_id")
            .execute()
        )
        return True
    except Exception:
        log.exception("Failed to save VoiceMaster hub config")
        return False


async def get_hub(*, guild_id: int) -> dict | None:
    client = get_client()
    if not client:
        return None

    try:
        res = await _run(
            lambda: client.table("voicemaster_hubs")
            .select("*")
            .eq("guild_id", str(guild_id))
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None
    except Exception:
        log.exception("Failed to fetch VoiceMaster hub config")
        return None


async def delete_hub(*, guild_id: int) -> bool:
    client = get_client()
    if not client:
        return False

    try:
        await _run(
            lambda: client.table("voicemaster_hubs")
            .delete()
            .eq("guild_id", str(guild_id))
            .execute()
        )
        return True
    except Exception:
        log.exception("Failed to delete VoiceMaster hub config")
        return False


# ── Live temp channels ───────────────────────────────────────────

async def create_temp_channel(
    *, guild_id: int, channel_id: int, owner_id: int
) -> bool:
    client = get_client()
    if not client:
        return False

    payload = {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
        "owner_id": str(owner_id),
        "join_order": [],
    }

    try:
        await _run(lambda: client.table("voicemaster_channels").insert(payload).execute())
        return True
    except Exception:
        log.exception("Failed to record new temp voice channel")
        return False


async def get_temp_channel(*, channel_id: int) -> dict | None:
    client = get_client()
    if not client:
        return None

    try:
        res = await _run(
            lambda: client.table("voicemaster_channels")
            .select("*")
            .eq("channel_id", str(channel_id))
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None
    except Exception:
        log.exception("Failed to fetch temp voice channel")
        return None


async def update_temp_channel(*, channel_id: int, **fields) -> bool:
    """Generic partial update, e.g. update_temp_channel(channel_id=x, owner_id=y, locked=True)."""
    client = get_client()
    if not client:
        return False
    if not fields:
        return True

    clean = {}
    for k, v in fields.items():
        if k == "owner_id" and v is not None:
            v = str(v)
        clean[k] = v

    try:
        await _run(
            lambda: client.table("voicemaster_channels")
            .update(clean)
            .eq("channel_id", str(channel_id))
            .execute()
        )
        return True
    except Exception:
        log.exception("Failed to update temp voice channel")
        return False


async def delete_temp_channel(*, channel_id: int) -> bool:
    client = get_client()
    if not client:
        return False

    try:
        await _run(
            lambda: client.table("voicemaster_channels")
            .delete()
            .eq("channel_id", str(channel_id))
            .execute()
        )
        return True
    except Exception:
        log.exception("Failed to delete temp voice channel record")
        return False


async def get_all_temp_channels(*, guild_id: int) -> list[dict]:
    client = get_client()
    if not client:
        return []

    try:
        res = await _run(
            lambda: client.table("voicemaster_channels")
            .select("*")
            .eq("guild_id", str(guild_id))
            .execute()
        )
        return res.data or []
    except Exception:
        log.exception("Failed to list temp voice channels")
        return []


# ── Per-user saved profiles ("Load Settings") ────────────────────

async def save_profile(
    *,
    guild_id: int,
    user_id: int,
    channel_name: str | None = None,
    user_limit: int | None = None,
    locked: bool | None = None,
    ghosted: bool | None = None,
    permitted_ids: list[int] | None = None,
    rejected_ids: list[int] | None = None,
) -> bool:
    client = get_client()
    if not client:
        return False

    payload = {"guild_id": str(guild_id), "user_id": str(user_id)}
    if channel_name is not None:
        payload["channel_name"] = channel_name
    if user_limit is not None:
        payload["user_limit"] = user_limit
    if locked is not None:
        payload["locked"] = locked
    if ghosted is not None:
        payload["ghosted"] = ghosted
    if permitted_ids is not None:
        payload["permitted_ids"] = [str(i) for i in permitted_ids]
    if rejected_ids is not None:
        payload["rejected_ids"] = [str(i) for i in rejected_ids]

    try:
        await _run(
            lambda: client.table("voicemaster_profiles")
            .upsert(payload, on_conflict="guild_id,user_id")
            .execute()
        )
        return True
    except Exception:
        log.exception("Failed to save VoiceMaster profile")
        return False


async def get_profile(*, guild_id: int, user_id: int) -> dict | None:
    client = get_client()
    if not client:
        return None

    try:
        res = await _run(
            lambda: client.table("voicemaster_profiles")
            .select("*")
            .eq("guild_id", str(guild_id))
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None
    except Exception:
        log.exception("Failed to fetch VoiceMaster profile")
        return None    