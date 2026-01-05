"""Briefing generation endpoints."""

import json
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from pathlib import Path
from datetime import datetime

from briefly.services.curation import CurationService
from briefly.services.transcripts import get_transcript_store, get_transcript_processor

router = APIRouter()

# Simple file-based storage
SOURCES_FILE = Path(__file__).parent.parent.parent.parent.parent / ".cache" / "sources.json"
BRIEFINGS_FILE = Path(__file__).parent.parent.parent.parent.parent / ".cache" / "briefings.json"

# In-memory job status
_jobs: dict[str, dict] = {}


def _load_sources() -> dict:
    if SOURCES_FILE.exists():
        return json.loads(SOURCES_FILE.read_text())
    return {"x": []}


def _load_briefings() -> list:
    if BRIEFINGS_FILE.exists():
        return json.loads(BRIEFINGS_FILE.read_text())
    return []


def _save_briefing(briefing: dict):
    # Ensure briefing has a unique ID (use job_id if available, else generate one)
    if "id" not in briefing:
        briefing["id"] = briefing.get("job_id") or datetime.now().strftime("%Y%m%d_%H%M%S")
    briefings = _load_briefings()
    briefings.insert(0, briefing)  # Most recent first
    briefings = briefings[:20]  # Keep last 20
    BRIEFINGS_FILE.parent.mkdir(exist_ok=True)
    BRIEFINGS_FILE.write_text(json.dumps(briefings, indent=2, default=str))


class GenerateRequest(BaseModel):
    hours_back: int = 24


@router.get("")
async def list_briefings() -> list:
    """List recent briefings."""
    return _load_briefings()


@router.post("/generate")
async def generate_briefing(req: GenerateRequest, background_tasks: BackgroundTasks) -> dict:
    """Generate a new briefing from configured sources."""
    sources = _load_sources()
    x_sources = sources.get("x", [])
    youtube_sources = sources.get("youtube", [])

    if not x_sources and not youtube_sources:
        raise HTTPException(400, "No sources configured. Add sources first.")

    job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    _jobs[job_id] = {
        "status": "processing",
        "started_at": datetime.now().isoformat(),
        "step": "Starting...",
        "sources": {"x": len(x_sources), "youtube": len(youtube_sources)},
    }

    async def run_briefing():
        try:
            _jobs[job_id]["step"] = f"Fetching from {len(x_sources)} X + {len(youtube_sources)} YouTube sources..."

            service = CurationService()
            result = await service.create_briefing(
                x_sources=x_sources if x_sources else None,
                youtube_sources=youtube_sources if youtube_sources else None,
                hours_back=req.hours_back,
            )

            _jobs[job_id]["step"] = "Generating AI summary..."

            result["generated_at"] = datetime.now().isoformat()
            result["job_id"] = job_id
            _save_briefing(result)
            _jobs[job_id] = {"status": "completed", "result": result}
        except Exception as e:
            import traceback
            _jobs[job_id] = {"status": "failed", "error": str(e), "traceback": traceback.format_exc()}

    background_tasks.add_task(run_briefing)

    return {
        "job_id": job_id,
        "status": "processing",
        "sources": {"x": x_sources, "youtube": youtube_sources}
    }


@router.get("/generate/{job_id}")
async def get_job_status(job_id: str) -> dict:
    """Get status of a briefing generation job."""
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    return _jobs[job_id]


@router.get("/latest")
async def get_latest_briefing() -> dict:
    """Get the most recent briefing."""
    briefings = _load_briefings()
    if not briefings:
        raise HTTPException(404, "No briefings yet")
    return briefings[0]


# Transcript management endpoints

@router.get("/transcripts/stats")
async def transcript_stats() -> dict:
    """Get transcript storage statistics."""
    store = get_transcript_store()
    pending = store.list_pending()
    return {
        "pending_summarization": len(pending),
        "pending_video_ids": pending[:10],  # Show first 10
    }


@router.post("/transcripts/process")
async def process_transcripts(background_tasks: BackgroundTasks, limit: int = 5) -> dict:
    """
    Process pending transcripts in the background.

    This summarizes transcripts that have been stored but not yet processed.
    """
    store = get_transcript_store()
    pending = store.list_pending()

    if not pending:
        return {"status": "no_pending", "message": "No transcripts pending summarization"}

    job_id = f"transcripts_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    _jobs[job_id] = {
        "status": "processing",
        "type": "transcript_summarization",
        "started_at": datetime.now().isoformat(),
        "pending_count": len(pending),
        "limit": limit,
    }

    async def run_processing():
        try:
            processor = get_transcript_processor()
            processed = await processor.process_pending(limit=limit)
            _jobs[job_id] = {
                "status": "completed",
                "processed_count": processed,
                "remaining": len(store.list_pending()),
            }
        except Exception as e:
            import traceback
            _jobs[job_id] = {
                "status": "failed",
                "error": str(e),
                "traceback": traceback.format_exc(),
            }

    background_tasks.add_task(run_processing)

    return {
        "job_id": job_id,
        "status": "processing",
        "pending_count": len(pending),
        "processing_limit": limit,
    }
