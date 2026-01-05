"""Podcast source management endpoints."""

import json
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathlib import Path

from briefly.adapters.podcast import PodcastAdapter
from briefly.core.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Store podcast sources alongside other sources
SOURCES_FILE = Path(__file__).parent.parent.parent.parent.parent / ".cache" / "sources.json"


def _load_sources() -> dict:
    """Load sources from file."""
    if SOURCES_FILE.exists():
        return json.loads(SOURCES_FILE.read_text())
    return {"x": [], "youtube": [], "podcasts": []}


def _save_sources(sources: dict):
    """Save sources to file."""
    SOURCES_FILE.parent.mkdir(exist_ok=True)
    SOURCES_FILE.write_text(json.dumps(sources, indent=2))


class SearchPodcastsRequest(BaseModel):
    query: str
    limit: int = 10


class AddPodcastRequest(BaseModel):
    identifier: str  # UUID, iTunes ID, or name


@router.get("/status")
async def podcast_status() -> dict:
    """Check if podcast API is configured."""
    settings = get_settings()
    return {
        "configured": bool(settings.taddy_api_key and settings.taddy_user_id),
        "api_key_set": bool(settings.taddy_api_key),
        "user_id_set": bool(settings.taddy_user_id),
    }


@router.post("/search")
async def search_podcasts(req: SearchPodcastsRequest) -> dict:
    """Search for podcasts by name or topic."""
    adapter = PodcastAdapter()
    results = await adapter.search_podcasts(req.query, limit=req.limit)

    return {
        "query": req.query,
        "count": len(results),
        "results": results,
    }


@router.get("")
async def list_podcasts() -> list:
    """List configured podcast sources."""
    sources = _load_sources()
    return sources.get("podcasts", [])


@router.post("")
async def add_podcast(req: AddPodcastRequest) -> dict:
    """Add a podcast source."""
    adapter = PodcastAdapter()

    # Look up podcast to verify it exists
    podcast = await adapter.lookup_podcast(req.identifier)
    if not podcast:
        raise HTTPException(404, f"Podcast not found: {req.identifier}")

    sources = _load_sources()
    if "podcasts" not in sources:
        sources["podcasts"] = []

    # Check if already exists
    existing_ids = [p["id"] for p in sources["podcasts"]]
    if podcast["id"] in existing_ids:
        raise HTTPException(400, f"Podcast '{podcast['name']}' already added")

    # Add to sources
    sources["podcasts"].append({
        "id": podcast["id"],
        "name": podcast["name"],
        "author": podcast.get("author"),
        "image_url": podcast.get("image_url"),
        "episode_count": podcast.get("episode_count", 0),
    })
    _save_sources(sources)

    return {
        "status": "added",
        "podcast": podcast,
    }


@router.delete("/{podcast_id}")
async def remove_podcast(podcast_id: str) -> dict:
    """Remove a podcast source."""
    sources = _load_sources()

    podcasts = sources.get("podcasts", [])
    for i, p in enumerate(podcasts):
        if p["id"] == podcast_id:
            removed = podcasts.pop(i)
            sources["podcasts"] = podcasts
            _save_sources(sources)
            return {"status": "removed", "podcast": removed}

    raise HTTPException(404, "Podcast not found in sources")


@router.get("/{podcast_id}/episodes")
async def get_podcast_episodes(podcast_id: str, limit: int = 5) -> dict:
    """
    Get recent episodes from a podcast with transcript status.

    Each episode includes:
    - transcript_status: "available" | "audio_only" | "unavailable"
    - has_transcript: True if Taddy has transcript
    - can_transcribe_locally: True if audio available for local Whisper
    """
    adapter = PodcastAdapter()

    # First verify podcast exists
    podcast = await adapter.lookup_podcast(podcast_id)
    if not podcast:
        raise HTTPException(404, f"Podcast not found: {podcast_id}")

    episodes = await adapter.get_episodes(
        podcast_uuid=podcast["id"],
        limit=limit,
    )

    # Summarize transcript availability
    summary = {
        "available": sum(1 for e in episodes if e.get("transcript_status") == "available"),
        "audio_only": sum(1 for e in episodes if e.get("transcript_status") == "audio_only"),
        "unavailable": sum(1 for e in episodes if e.get("transcript_status") == "unavailable"),
    }

    return {
        "podcast": podcast,
        "count": len(episodes),
        "transcript_summary": summary,
        "episodes": episodes,
    }


@router.get("/{podcast_id}")
async def get_podcast_details(podcast_id: str) -> dict:
    """Get details for a specific podcast."""
    adapter = PodcastAdapter()
    podcast = await adapter.lookup_podcast(podcast_id)

    if not podcast:
        raise HTTPException(404, f"Podcast not found: {podcast_id}")

    return podcast


# --- Local Transcription Endpoints ---

class TranscribeRequest(BaseModel):
    audio_url: str
    model: str = "distil-medium.en"


@router.get("/transcription/models")
async def list_transcription_models() -> list:
    """List available local transcription models."""
    try:
        from briefly.services.transcription import LocalTranscriber
        return LocalTranscriber.available_models()
    except ImportError:
        raise HTTPException(
            503,
            "Local transcription not available. Install with: pip install 'briefly[transcription]'"
        )


@router.post("/transcription/transcribe")
async def transcribe_audio(req: TranscribeRequest) -> dict:
    """
    Transcribe audio from a URL using local Whisper.

    This uses Lightning Whisper MLX optimized for Apple Silicon.
    First call may take a few seconds to load the model.
    """
    try:
        from briefly.services.transcription import transcribe_podcast_episode
    except ImportError:
        raise HTTPException(
            503,
            "Local transcription not available. Install with: pip install 'briefly[transcription]'"
        )

    try:
        logger.info(f"Transcribing audio with model: {req.model}")
        transcript = await transcribe_podcast_episode(req.audio_url, model=req.model)
        return {
            "status": "success",
            "model": req.model,
            "transcript": transcript,
            "audio_url": req.audio_url,
        }
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise HTTPException(500, f"Transcription failed: {e}")
