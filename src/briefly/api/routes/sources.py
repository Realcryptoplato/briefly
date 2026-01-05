"""Source management endpoints."""

import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathlib import Path

from briefly.core.cache import get_user_cache
from briefly.adapters.x import XAdapter
from briefly.adapters.youtube import YouTubeAdapter
from briefly.services.x_lists import get_list_manager

router = APIRouter()

# Simple file-based source storage for now
SOURCES_FILE = Path(__file__).parent.parent.parent.parent.parent / ".cache" / "sources.json"


def _load_sources() -> dict:
    """Load sources from file."""
    if SOURCES_FILE.exists():
        data = json.loads(SOURCES_FILE.read_text())
        # Migrate old format (list of strings) to new format (list of dicts)
        if data.get("x") and isinstance(data["x"], list):
            if data["x"] and isinstance(data["x"][0], str):
                # Migrate to new format
                data["x"] = [
                    {"identifier": s, "list_synced": False}
                    for s in data["x"]
                ]
        return data
    return {"x": [], "youtube": [], "x_list_id": None, "x_list_last_sync": None}


def _save_sources(sources: dict):
    """Save sources to file."""
    SOURCES_FILE.parent.mkdir(exist_ok=True)
    SOURCES_FILE.write_text(json.dumps(sources, indent=2))


def _get_x_identifiers(sources: dict) -> list[str]:
    """Extract X usernames from sources (handles both old and new format)."""
    x_sources = sources.get("x", [])
    if not x_sources:
        return []
    # Handle both string list and dict list format
    if isinstance(x_sources[0], str):
        return x_sources
    return [s["identifier"] for s in x_sources if isinstance(s, dict)]


class AddSourceRequest(BaseModel):
    platform: str = "x"
    identifier: str  # username for X


class SourceResponse(BaseModel):
    platform: str
    identifier: str
    name: str | None = None
    cached: bool = False


@router.get("")
async def list_sources() -> dict:
    """List all configured sources."""
    sources = _load_sources()
    cache = get_user_cache()

    result = {
        "x": [],
        "youtube": [],
        "x_list_id": sources.get("x_list_id"),
        "x_list_last_sync": sources.get("x_list_last_sync"),
    }

    # X sources (handle both old and new format)
    x_sources = sources.get("x", [])
    for source in x_sources:
        # Handle both string and dict format
        if isinstance(source, str):
            username = source
            list_synced = False
        else:
            username = source.get("identifier", "")
            list_synced = source.get("list_synced", False)

        cached_user = cache.get(username)
        result["x"].append({
            "identifier": username,
            "name": cached_user.get("name") if cached_user else None,
            "user_id": cached_user.get("id") if cached_user else None,
            "cached": cached_user is not None,
            "list_synced": list_synced,
        })

    # YouTube sources
    for channel in sources.get("youtube", []):
        # Try both cache key formats
        cached_channel = cache.get(f"yt:{channel}") or cache.get(channel)
        result["youtube"].append({
            "identifier": channel,
            "name": cached_channel.get("name") if cached_channel else channel,
            "channel_id": cached_channel.get("id") if cached_channel else channel,
            "cached": cached_channel is not None,
        })

    return result


@router.post("")
async def add_source(req: AddSourceRequest) -> dict:
    """Add a new source."""
    sources = _load_sources()

    if req.platform == "x":
        username = req.identifier.lower().lstrip("@")

        # Check if already exists (handle both formats)
        existing = _get_x_identifiers(sources)
        if username in existing:
            raise HTTPException(400, f"Source @{username} already exists")

        adapter = XAdapter()
        user = await adapter.lookup_user(username)

        if not user:
            raise HTTPException(404, f"X user @{username} not found")

        if "x" not in sources:
            sources["x"] = []

        # Add with new format
        from datetime import datetime as dt
        source_entry = {
            "identifier": username,
            "added_at": dt.now().isoformat(),
            "list_synced": False,
            "list_sync_error": None,
        }
        sources["x"].append(source_entry)
        _save_sources(sources)

        # Attempt to add to list immediately (non-blocking)
        list_synced = False
        try:
            list_manager = get_list_manager()
            list_id = await list_manager.get_list_id()
            if list_id and user.get("id"):
                if await list_manager.add_member(str(user["id"])):
                    list_synced = True
                    # Update sync status
                    for s in sources["x"]:
                        if isinstance(s, dict) and s.get("identifier") == username:
                            s["list_synced"] = True
                    _save_sources(sources)
        except Exception:
            pass  # Non-critical, sync will happen on next fetch

        return {
            "status": "added",
            "source": {
                "platform": "x",
                "identifier": username,
                "name": user.get("name"),
                "user_id": user.get("id"),
                "list_synced": list_synced,
            }
        }

    elif req.platform == "youtube":
        channel = req.identifier.lstrip("@")

        if channel in sources.get("youtube", []):
            raise HTTPException(400, f"YouTube channel {channel} already exists")

        adapter = YouTubeAdapter()
        channel_info = await adapter.lookup_user(req.identifier)

        if not channel_info:
            raise HTTPException(404, f"YouTube channel {channel} not found")

        if "youtube" not in sources:
            sources["youtube"] = []
        sources["youtube"].append(channel)
        _save_sources(sources)

        return {
            "status": "added",
            "source": {
                "platform": "youtube",
                "identifier": channel,
                "name": channel_info.get("name"),
                "channel_id": channel_info.get("id"),
            }
        }

    else:
        raise HTTPException(400, f"Platform {req.platform} not supported")


@router.delete("/{platform}/{identifier}")
async def remove_source(platform: str, identifier: str) -> dict:
    """Remove a source."""
    if platform != "x":
        raise HTTPException(400, "Only X platform supported currently")

    username = identifier.lower().lstrip("@")
    sources = _load_sources()

    # Find and remove source (handle both old and new format)
    existing = _get_x_identifiers(sources)
    if username not in existing:
        raise HTTPException(404, f"Source @{username} not found")

    # Remove from sources (handle both formats)
    x_sources = sources.get("x", [])
    sources["x"] = [
        s for s in x_sources
        if (isinstance(s, str) and s != username) or
           (isinstance(s, dict) and s.get("identifier") != username)
    ]
    _save_sources(sources)

    # Attempt to remove from list (non-blocking)
    list_removed = False
    try:
        cache = get_user_cache()
        cached_user = cache.get(username)
        if cached_user and cached_user.get("id"):
            list_manager = get_list_manager()
            list_id = await list_manager.get_list_id()
            if list_id:
                list_removed = await list_manager.remove_member(str(cached_user["id"]))
    except Exception:
        pass  # Non-critical

    return {
        "status": "removed",
        "identifier": username,
        "list_removed": list_removed,
    }


@router.get("/cache/stats")
async def cache_stats() -> dict:
    """Get cache statistics."""
    cache = get_user_cache()
    return {
        "cached_users": len(cache._cache),
        "users": list(cache._cache.keys()),
    }


class ImportYouTubeRequest(BaseModel):
    channel: str  # YouTube channel handle or ID


@router.post("/youtube/import")
async def import_youtube_subscriptions(req: ImportYouTubeRequest) -> dict:
    """
    Import YouTube subscriptions from a channel.

    Give us a YouTube channel handle (@mkbhd) or ID,
    and we'll fetch all their public subscriptions.
    """
    adapter = YouTubeAdapter()

    # Check if channel exists
    channel = await adapter.lookup_user(req.channel)
    if not channel:
        raise HTTPException(404, f"YouTube channel {req.channel} not found")

    # Fetch subscriptions
    subs = await adapter.get_subscriptions(req.channel, max_results=100)

    if not subs:
        raise HTTPException(
            400,
            f"No subscriptions found for {channel['name']}. "
            "They may have set their subscriptions to private."
        )

    # Add to sources and cache names
    sources = _load_sources()
    if "youtube" not in sources:
        sources["youtube"] = []

    cache = get_user_cache()
    added = 0
    for sub in subs:
        channel_id = sub['channel_id']
        if channel_id not in sources["youtube"]:
            sources["youtube"].append(channel_id)
            # Cache the channel info so we can show names
            cache.set(f"yt:{channel_id}", {
                'id': channel_id,
                'name': sub['title'],
                'description': sub.get('description', ''),
            })
            added += 1

    _save_sources(sources)

    return {
        "status": "imported",
        "channel": channel['name'],
        "subscriptions_found": len(subs),
        "new_sources_added": added,
        "total_youtube_sources": len(sources["youtube"]),
    }


# X Lists Management Endpoints


@router.get("/x/list-status")
async def get_x_list_status() -> dict:
    """
    Get status of the X list used for efficient timeline fetching.

    Returns list ID, member count, sync status, and rate limit info.
    """
    list_manager = get_list_manager()
    return list_manager.get_status()


class XListSyncRequest(BaseModel):
    force: bool = False  # Force full resync even if already synced


@router.post("/x/sync")
async def sync_x_list(req: XListSyncRequest | None = None) -> dict:
    """
    Manually trigger sync of X sources to the persistent list.

    Adds any sources not in the list, removes stale sources.
    Respects rate limits (300 member adds/removes per 15 min).
    """
    sources = _load_sources()
    x_identifiers = _get_x_identifiers(sources)

    if not x_identifiers:
        return {
            "status": "no_sources",
            "message": "No X sources configured to sync",
        }

    list_manager = get_list_manager()

    try:
        # Ensure list exists
        list_id = await list_manager.ensure_list_exists()

        # Sync sources
        result = await list_manager.sync_sources(x_identifiers)

        # Update sources.json with sync status
        from datetime import datetime as dt
        synced_usernames = set(result.get("added", []) + result.get("already_synced", []))
        failed_usernames = set(result.get("failed", []))

        for source in sources.get("x", []):
            if isinstance(source, dict):
                username = source.get("identifier", "").lower()
                if username in synced_usernames:
                    source["list_synced"] = True
                    source["list_sync_error"] = None
                elif username in failed_usernames:
                    source["list_synced"] = False
                    source["list_sync_error"] = "Failed to add to list"

        sources["x_list_id"] = list_id
        sources["x_list_last_sync"] = dt.now().isoformat()
        _save_sources(sources)

        return {
            "status": "synced",
            "list_id": list_id,
            "result": result,
            "total_sources": len(x_identifiers),
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to sync X list: {str(e)}")


@router.get("/x/list-members")
async def get_x_list_members() -> dict:
    """
    Get current members of the X list.

    Useful for debugging and verifying sync status.
    """
    list_manager = get_list_manager()

    # Ensure list exists
    list_id = await list_manager.get_list_id()
    if not list_id:
        return {
            "status": "no_list",
            "members": [],
        }

    members = await list_manager.get_list_members()

    return {
        "status": "ok",
        "list_id": list_id,
        "member_count": len(members),
        "members": members,
    }
