"""Briefing generation endpoints."""

import json
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from pathlib import Path
from datetime import datetime

from briefly.services.curation import CurationService
from briefly.services.transcripts import get_transcript_store, get_transcript_processor
from briefly.services.jobs import get_job_service, JobType

router = APIRouter()

# Simple file-based storage
SOURCES_FILE = Path(__file__).parent.parent.parent.parent.parent / ".cache" / "sources.json"
BRIEFINGS_FILE = Path(__file__).parent.parent.parent.parent.parent / ".cache" / "briefings.json"
CATEGORIES_FILE = Path(__file__).parent.parent.parent.parent.parent / ".cache" / "categories.json"


def _load_sources() -> dict:
    if SOURCES_FILE.exists():
        return json.loads(SOURCES_FILE.read_text())
    return {"x": []}


def _load_briefings() -> list:
    if BRIEFINGS_FILE.exists():
        return json.loads(BRIEFINGS_FILE.read_text())
    return []


def _load_categories() -> list:
    if CATEGORIES_FILE.exists():
        return json.loads(CATEGORIES_FILE.read_text())
    return []


def _get_sources_for_categories(category_ids: list[str]) -> dict:
    """Get combined sources from specified categories."""
    categories = _load_categories()
    x_sources = set()
    youtube_sources = set()
    podcast_sources = set()

    for cat in categories:
        if cat["id"] in category_ids:
            x_sources.update(cat.get("sources", {}).get("x", []))
            youtube_sources.update(cat.get("sources", {}).get("youtube", []))
            podcast_sources.update(cat.get("sources", {}).get("podcasts", []))

    return {
        "x": list(x_sources),
        "youtube": list(youtube_sources),
        "podcasts": list(podcast_sources),
    }


def _save_briefing(briefing: dict):
    briefings = _load_briefings()
    briefings.insert(0, briefing)  # Most recent first
    briefings = briefings[:20]  # Keep last 20
    BRIEFINGS_FILE.parent.mkdir(exist_ok=True)
    BRIEFINGS_FILE.write_text(json.dumps(briefings, indent=2, default=str))


class GenerateRequest(BaseModel):
    hours_back: int = 24
    category_ids: list[str] | None = None  # Filter by specific categories


@router.get("")
async def list_briefings() -> list:
    """List recent briefings."""
    return _load_briefings()


@router.post("/generate")
async def generate_briefing(req: GenerateRequest, background_tasks: BackgroundTasks) -> dict:
    """Generate a new briefing from configured sources or categories."""
    job_service = get_job_service()

    # If categories specified, use sources from those categories
    if req.category_ids:
        cat_sources = _get_sources_for_categories(req.category_ids)
        x_sources = cat_sources["x"]
        youtube_sources = cat_sources["youtube"]
        podcast_sources = cat_sources.get("podcasts", [])
        categories = _load_categories()
        category_names = [c["name"] for c in categories if c["id"] in req.category_ids]
    else:
        # Use all configured sources
        sources = _load_sources()
        x_sources = sources.get("x", [])
        youtube_sources = sources.get("youtube", [])
        # Get podcast source IDs from the podcasts list
        podcast_sources = [p["id"] for p in sources.get("podcasts", [])]
        category_names = None

    if not x_sources and not youtube_sources and not podcast_sources:
        raise HTTPException(400, "No sources configured. Add sources first.")

    # Create job in database
    job = await job_service.create(
        job_type=JobType.BRIEFING.value,
        params={
            "hours_back": req.hours_back,
            "category_ids": req.category_ids,
            "sources": {
                "x": x_sources,
                "youtube": youtube_sources,
                "podcasts": podcast_sources,
            },
            "category_names": category_names,
        },
        source="api",
    )
    job_id = job.id

    # Initialize progress
    initial_progress = {
        "step": "Starting...",
        "step_detail": None,
        "current": 0,
        "total": 0,
        "elapsed_seconds": 0,
        "sources": {"x": len(x_sources), "youtube": len(youtube_sources), "podcasts": len(podcast_sources)},
        "categories": category_names,
        "media_status": {
            "x": {"status": "pending", "count": 0, "sources": len(x_sources)},
            "youtube": {"status": "pending", "count": 0, "sources": len(youtube_sources)},
            "podcasts": {"status": "pending", "count": 0, "sources": len(podcast_sources)},
        },
    }
    await job_service.update_progress(job_id, initial_progress)

    async def progress_callback(step: str, current: int, total: int, elapsed: float, media_status: dict | None = None) -> None:
        """Update job status with progress info."""
        progress = {
            "step": step,
            "current": current,
            "total": total,
            "elapsed_seconds": round(elapsed, 1),
        }
        if media_status:
            progress["media_status"] = media_status
        # Estimate remaining time based on progress
        if current > 0 and total > 0:
            rate = elapsed / current
            remaining = (total - current) * rate
            progress["eta_seconds"] = round(remaining, 1)
        await job_service.update_progress(job_id, progress)

    async def run_briefing():
        try:
            service = CurationService()
            result = await service.create_briefing(
                x_sources=x_sources if x_sources else None,
                youtube_sources=youtube_sources if youtube_sources else None,
                podcast_sources=podcast_sources if podcast_sources else None,
                hours_back=req.hours_back,
                transcribe_locally=True,  # Use local Whisper for podcasts
                progress_callback=progress_callback,
            )

            result["generated_at"] = datetime.now().isoformat()
            result["job_id"] = job_id
            if category_names:
                result["categories"] = category_names
            _save_briefing(result)
            await job_service.complete(job_id, {"result": result})
        except Exception as e:
            import traceback
            await job_service.fail(job_id, f"{str(e)}\n{traceback.format_exc()}")

    background_tasks.add_task(run_briefing)

    return {
        "job_id": job_id,
        "status": "pending",
        "sources": {"x": x_sources, "youtube": youtube_sources, "podcasts": podcast_sources}
    }


@router.get("/generate/{job_id}")
async def get_job_status(job_id: str) -> dict:
    """Get status of a briefing generation job."""
    job_service = get_job_service()
    job = await job_service.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    # Return in backward-compatible format
    result = {
        "id": job.id,
        "type": job.type,
        "status": job.status,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }

    # Include progress details
    if job.progress:
        result.update(job.progress)

    # Include output if completed
    if job.output:
        result["result"] = job.output.get("result")

    # Include error if failed
    if job.error:
        result["error"] = job.error

    return result


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
    job_service = get_job_service()
    store = get_transcript_store()
    pending = store.list_pending()

    if not pending:
        return {"status": "no_pending", "message": "No transcripts pending summarization"}

    # Create job in database
    job = await job_service.create(
        job_type=JobType.TRANSCRIPTION.value,
        params={
            "pending_count": len(pending),
            "limit": limit,
        },
        source="api",
    )
    job_id = job.id

    # Initialize progress
    await job_service.update_progress(job_id, {
        "pending_count": len(pending),
        "limit": limit,
    })

    async def run_processing():
        try:
            processor = get_transcript_processor()
            processed = await processor.process_pending(limit=limit)
            await job_service.complete(job_id, {
                "processed_count": processed,
                "remaining": len(store.list_pending()),
            })
        except Exception as e:
            import traceback
            await job_service.fail(job_id, f"{str(e)}\n{traceback.format_exc()}")

    background_tasks.add_task(run_processing)

    return {
        "job_id": job_id,
        "status": "pending",
        "pending_count": len(pending),
        "processing_limit": limit,
    }


# Job management endpoints

@router.get("/jobs")
async def list_jobs(limit: int = 20) -> list[dict]:
    """List recent jobs."""
    job_service = get_job_service()
    jobs = await job_service.list_recent(limit)
    return [job.to_dict() for job in jobs]


@router.get("/jobs/active")
async def get_active_job() -> dict | None:
    """Get currently active job if any."""
    job_service = get_job_service()
    job = await job_service.get_active()
    if not job:
        return {"active": False, "job": None}
    return {"active": True, "job": job.to_dict()}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    """Get job by ID."""
    job_service = get_job_service()
    job = await job_service.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()
