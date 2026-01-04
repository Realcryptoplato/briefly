"""FastAPI application."""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import logging

from briefly.api.routes import sources, briefings, health, search, categories, settings, podcasts
from briefly.services.jobs import get_job_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler - runs on startup/shutdown."""
    # Startup
    job_service = get_job_service()
    await job_service.init()
    logger.info(f"JobService initialized ({job_service.db_type} backend)")

    yield

    # Shutdown (nothing needed for now)


app = FastAPI(
    title="Briefly 3000",
    description="AI-powered media curation and daily briefings",
    version="0.1.0",
    lifespan=lifespan,
)

# Templates
templates_dir = Path(__file__).parent / "templates"
templates_dir.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(templates_dir))

# Include routers
app.include_router(health.router, tags=["Health"])
app.include_router(sources.router, prefix="/api/sources", tags=["Sources"])
app.include_router(briefings.router, prefix="/api/briefings", tags=["Briefings"])
app.include_router(search.router, prefix="/api/search", tags=["Search"])
app.include_router(categories.router, prefix="/api/categories", tags=["Categories"])
app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])
app.include_router(podcasts.router, prefix="/api/podcasts", tags=["Podcasts"])


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    return templates.TemplateResponse("dashboard.html", {"request": request})
