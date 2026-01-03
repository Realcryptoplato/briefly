"""Source management endpoints."""

import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathlib import Path

from briefly.core.cache import get_user_cache
from briefly.adapters.x import XAdapter
from briefly.adapters.youtube import YouTubeAdapter

router = APIRouter()

# Simple file-based source storage for now
SOURCES_FILE = Path(__file__).parent.parent.parent.parent.parent / ".cache" / "sources.json"


def _load_sources() -> dict:
    """Load sources from file."""
    if SOURCES_FILE.exists():
        return json.loads(SOURCES_FILE.read_text())
    return {"x": [], "youtube": []}


def _save_sources(sources: dict):
    """Save sources to file."""
    SOURCES_FILE.parent.mkdir(exist_ok=True)
    SOURCES_FILE.write_text(json.dumps(sources, indent=2))


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

    result = {"x": [], "youtube": []}

    # X sources
    for username in sources.get("x", []):
        cached_user = cache.get(username)
        result["x"].append({
            "identifier": username,
            "name": cached_user.get("name") if cached_user else None,
            "user_id": cached_user.get("id") if cached_user else None,
            "cached": cached_user is not None,
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

        if username in sources.get("x", []):
            raise HTTPException(400, f"Source @{username} already exists")

        adapter = XAdapter()
        user = await adapter.lookup_user(username)

        if not user:
            raise HTTPException(404, f"X user @{username} not found")

        if "x" not in sources:
            sources["x"] = []
        sources["x"].append(username)
        _save_sources(sources)

        return {
            "status": "added",
            "source": {
                "platform": "x",
                "identifier": username,
                "name": user.get("name"),
                "user_id": user.get("id"),
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

    if username not in sources.get("x", []):
        raise HTTPException(404, f"Source @{username} not found")

    sources["x"].remove(username)
    _save_sources(sources)

    return {"status": "removed", "identifier": username}


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
