"""FastAPI application."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from briefly.api.routes import sources, briefings, health, search, jobs
from briefly.services.jobs import get_job_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - initialize services on startup."""
    # Initialize job service database schema
    job_service = get_job_service()
    await job_service.init()
    yield


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
app.include_router(jobs.router, prefix="/api/jobs", tags=["Jobs"])
app.include_router(jobs.n8n_router, prefix="/api/n8n", tags=["n8n Webhooks"])


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    return templates.TemplateResponse("dashboard.html", {"request": request})
