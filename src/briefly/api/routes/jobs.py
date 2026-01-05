"""Job management API endpoints.

Endpoints:
- POST /api/jobs - Create a new job
- GET /api/jobs/{job_id} - Get job status and progress
- GET /api/jobs/active - Get currently running job
- GET /api/jobs - List recent jobs
- POST /api/n8n/progress - Webhook for n8n progress updates
- POST /api/n8n/complete - Webhook for n8n completion
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from briefly.core.config import get_settings
from briefly.services.jobs import JobService, Job, JobStatus, get_job_service
from briefly.services.curation import CurationService


router = APIRouter()


# Request/Response Models


class CreateJobRequest(BaseModel):
    """Request to create a new job."""

    type: str = Field(..., description="Job type: briefing, transcription, extraction")
    params: dict[str, Any] = Field(
        default_factory=dict, description="Job parameters"
    )
    delegate_to_n8n: bool = Field(
        default=False, description="Whether to delegate to n8n"
    )


class CreateJobResponse(BaseModel):
    """Response after creating a job."""

    job_id: str
    status: str
    n8n_execution_id: Optional[str] = None


class JobResponse(BaseModel):
    """Full job response."""

    id: str
    type: str
    status: str
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    n8n_execution_id: Optional[str] = None
    n8n_workflow_id: Optional[str] = None
    progress: Optional[dict[str, Any]] = None
    input: Optional[dict[str, Any]] = None
    output: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    source: str = "local"


class N8NProgressRequest(BaseModel):
    """Request from n8n to update job progress."""

    job_id: str
    progress: dict[str, Any]


class N8NCompleteRequest(BaseModel):
    """Request from n8n to mark job complete."""

    job_id: str
    output: Optional[dict[str, Any]] = None
    error: Optional[str] = None


def _job_to_response(job: Job) -> JobResponse:
    """Convert Job dataclass to response model."""
    return JobResponse(
        id=job.id,
        type=job.type,
        status=job.status,
        created_at=job.created_at.isoformat() if job.created_at else None,
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        n8n_execution_id=job.n8n_execution_id,
        n8n_workflow_id=job.n8n_workflow_id,
        progress=job.progress,
        input=job.input,
        output=job.output,
        error=job.error,
        source=job.source,
    )


# Job Management Endpoints


@router.post("", response_model=CreateJobResponse)
async def create_job(
    req: CreateJobRequest,
    background_tasks: BackgroundTasks,
) -> CreateJobResponse:
    """
    Create a new job.

    If delegate_to_n8n is True, triggers the n8n webhook and tracks the execution.
    Otherwise, creates a local job that can be processed by background tasks.
    """
    service = get_job_service()

    source = "n8n" if req.delegate_to_n8n else "local"
    job = await service.create(req.type, req.params, source=source)

    n8n_execution_id = None

    if req.delegate_to_n8n:
        # Trigger n8n webhook
        async def trigger_n8n():
            try:
                settings = get_settings()
                async with httpx.AsyncClient(timeout=30.0) as client:
                    webhook_url = f"{settings.n8n_base_url}{settings.n8n_webhook_path}"
                    response = await client.post(
                        webhook_url,
                        json={
                            "job_id": job.id,
                            "type": req.type,
                            "params": req.params,
                        },
                    )
                    response.raise_for_status()

                    # Update job with n8n execution ID if returned
                    result = response.json()
                    if "executionId" in result:
                        await service.update_progress(
                            job.id,
                            {"n8n_triggered": True, "execution_id": result["executionId"]},
                        )
            except httpx.HTTPError as e:
                # If n8n fails, mark job as failed
                await service.fail(job.id, f"Failed to trigger n8n: {str(e)}")

        background_tasks.add_task(trigger_n8n)
    else:
        # Run local job
        async def run_local_job():
            import json
            import traceback
            from pathlib import Path

            try:
                await service.start(job.id)
                await service.update_progress(job.id, {"step": "Loading sources..."})

                # Load sources from cache
                sources_file = Path(__file__).parent.parent.parent.parent.parent / ".cache" / "sources.json"
                sources = {}
                if sources_file.exists():
                    sources = json.loads(sources_file.read_text())

                x_sources = sources.get("x", [])
                youtube_sources = sources.get("youtube", [])

                if not x_sources and not youtube_sources:
                    await service.fail(job.id, "No sources configured. Add sources first.")
                    return

                await service.update_progress(job.id, {
                    "step": f"Fetching from {len(x_sources)} X + {len(youtube_sources)} YouTube sources...",
                    "media_status": {
                        "x": {"status": "fetching", "count": 0, "sources": len(x_sources)},
                        "youtube": {"status": "fetching", "count": 0, "sources": len(youtube_sources)},
                    }
                })

                # Run the actual curation
                curation = CurationService()
                hours_back = req.params.get("hours_back", 24)

                result = await curation.create_briefing(
                    x_sources=x_sources if x_sources else None,
                    youtube_sources=youtube_sources if youtube_sources else None,
                    hours_back=hours_back,
                )

                await service.update_progress(job.id, {
                    "step": "Saving briefing...",
                    "media_status": {
                        "x": {"status": "complete", "count": result.get("stats", {}).get("items_fetched", {}).get("x", 0)},
                        "youtube": {"status": "complete", "count": result.get("stats", {}).get("items_fetched", {}).get("youtube", 0)},
                    }
                })

                # Save briefing to cache
                briefings_file = Path(__file__).parent.parent.parent.parent.parent / ".cache" / "briefings.json"
                briefings = []
                if briefings_file.exists():
                    briefings = json.loads(briefings_file.read_text())

                result["generated_at"] = datetime.now().isoformat()
                result["job_id"] = job.id
                briefings.insert(0, result)
                briefings = briefings[:20]
                briefings_file.write_text(json.dumps(briefings, indent=2, default=str))

                await service.complete(job.id, {"result": result})

            except Exception as e:
                await service.fail(job.id, f"{str(e)}\n{traceback.format_exc()}")

        background_tasks.add_task(run_local_job)

    return CreateJobResponse(
        job_id=job.id,
        status=job.status,
        n8n_execution_id=n8n_execution_id,
    )


@router.get("/active", response_model=Optional[JobResponse])
async def get_active_job() -> Optional[JobResponse]:
    """
    Get the currently running job (if any).

    Used by the frontend to reconnect to a running job after page reload.
    """
    service = get_job_service()
    job = await service.get_active()

    if not job:
        raise HTTPException(status_code=404, detail="No active job")

    return _job_to_response(job)


@router.get("", response_model=list[JobResponse])
async def list_jobs(
    limit: int = 20,
    status: Optional[str] = None,
) -> list[JobResponse]:
    """
    List recent jobs.

    Args:
        limit: Maximum number of jobs to return (default 20)
        status: Filter by status (optional)
    """
    service = get_job_service()
    jobs = await service.list_recent(limit=limit)

    # Filter by status if provided
    if status:
        jobs = [j for j in jobs if j.status == status]

    return [_job_to_response(j) for j in jobs]


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> JobResponse:
    """
    Get job status and progress by ID.

    Used for polling job progress from the frontend.
    """
    service = get_job_service()
    job = await service.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return _job_to_response(job)


# n8n Webhook Endpoints


n8n_router = APIRouter()


@n8n_router.post("/progress")
async def n8n_progress_webhook(req: N8NProgressRequest) -> dict[str, str]:
    """
    Webhook for n8n to push progress updates.

    Called by n8n workflows to update job progress during execution.
    """
    service = get_job_service()

    job = await service.get(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    await service.update_progress(req.job_id, req.progress)

    return {"status": "ok"}


@n8n_router.post("/complete")
async def n8n_complete_webhook(req: N8NCompleteRequest) -> dict[str, str]:
    """
    Webhook for n8n to mark job as complete.

    Called by n8n workflows when execution finishes (success or failure).
    """
    service = get_job_service()

    job = await service.get(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if req.error:
        await service.fail(req.job_id, req.error)
    else:
        await service.complete(req.job_id, req.output or {})

    return {"status": "ok"}
