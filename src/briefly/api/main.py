"""FastAPI application."""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from briefly.api.routes import sources, briefings, health

app = FastAPI(
    title="Briefly 3000",
    description="AI-powered media curation and daily briefings",
    version="0.1.0",
)

# Templates
templates_dir = Path(__file__).parent / "templates"
templates_dir.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(templates_dir))

# Include routers
app.include_router(health.router, tags=["Health"])
app.include_router(sources.router, prefix="/api/sources", tags=["Sources"])
app.include_router(briefings.router, prefix="/api/briefings", tags=["Briefings"])


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    return templates.TemplateResponse("dashboard.html", {"request": request})
