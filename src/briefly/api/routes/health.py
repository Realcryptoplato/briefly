"""Health check endpoints."""

from fastapi import APIRouter

from briefly.core.config import get_settings
from briefly.services.jobs import get_job_service

router = APIRouter()


@router.get("/health")
async def health_check():
    """Basic health check with environment info."""
    settings = get_settings()
    job_service = get_job_service()

    return {
        "status": "ok",
        "service": "briefly3000",
        "environment": settings.app_env,
        "is_production": settings.is_production,
        "database": job_service.db_type,
    }
