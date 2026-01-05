"""Settings API for runtime configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from briefly.core.config import get_settings

router = APIRouter()

# Settings file location (in project root)
SETTINGS_FILE = Path(__file__).parent.parent.parent.parent.parent / ".briefly-settings.json"

# VPS connection template (password from environment)
VPS_HOST = "100.101.168.91"
VPS_PORT = "5435"
VPS_DB = "briefly"
VPS_USER = "briefly"


class SettingsResponse(BaseModel):
    """Current settings state."""
    database_mode: str  # "local" or "vps"
    database_type: str  # "sqlite" or "postgresql"
    database_url_masked: Optional[str] = None
    environment: str
    is_production: bool
    restart_required: bool = False


class UpdateSettingsRequest(BaseModel):
    """Request to update settings."""
    database_mode: str  # "local" or "vps"


def _load_local_settings() -> dict:
    """Load settings from local file."""
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_local_settings(settings: dict) -> None:
    """Save settings to local file."""
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))


def _get_current_database_mode() -> str:
    """Determine current database mode based on DATABASE_URL."""
    db_url = os.environ.get("DATABASE_URL", "")
    if "100.101.168.91" in db_url or "5435" in db_url:
        return "vps"
    elif db_url.startswith("postgresql://"):
        return "local-pg"
    return "local"


def _mask_url(url: str) -> str:
    """Mask sensitive parts of database URL."""
    if not url:
        return "sqlite (default)"
    # Mask password
    import re
    return re.sub(r':([^:@]+)@', ':****@', url)


@router.get("", response_model=SettingsResponse)
async def get_current_settings() -> SettingsResponse:
    """Get current settings state."""
    settings = get_settings()
    local_settings = _load_local_settings()

    db_url = os.environ.get("DATABASE_URL", "")
    current_mode = _get_current_database_mode()
    preferred_mode = local_settings.get("database_mode", current_mode)

    # Check if restart is needed
    restart_required = preferred_mode != current_mode

    return SettingsResponse(
        database_mode=current_mode,
        database_type="postgresql" if db_url else "sqlite",
        database_url_masked=_mask_url(db_url),
        environment=settings.app_env,
        is_production=settings.is_production,
        restart_required=restart_required,
    )


@router.post("", response_model=SettingsResponse)
async def update_settings(req: UpdateSettingsRequest) -> SettingsResponse:
    """
    Update settings and provide restart instructions.

    Changes are saved to .briefly-settings.json.
    Server restart required to apply database changes.
    """
    if req.database_mode not in ("local", "vps"):
        raise HTTPException(status_code=400, detail="database_mode must be 'local' or 'vps'")

    # Save preference
    local_settings = _load_local_settings()
    local_settings["database_mode"] = req.database_mode
    _save_local_settings(local_settings)

    # Generate .env instructions
    current_mode = _get_current_database_mode()
    restart_required = req.database_mode != current_mode

    settings = get_settings()
    db_url = os.environ.get("DATABASE_URL", "")

    return SettingsResponse(
        database_mode=current_mode,  # Still showing current until restart
        database_type="postgresql" if db_url else "sqlite",
        database_url_masked=_mask_url(db_url),
        environment=settings.app_env,
        is_production=settings.is_production,
        restart_required=restart_required,
    )


@router.get("/env-command")
async def get_env_command(mode: str = "vps") -> dict:
    """Get the shell command to switch database modes."""
    if mode == "vps":
        # Password should be set via VPS_DB_PASSWORD env var or in .env
        vps_password = os.environ.get("VPS_DB_PASSWORD", "<your-vps-password>")
        vps_url = f"postgresql://{VPS_USER}:{vps_password}@{VPS_HOST}:{VPS_PORT}/{VPS_DB}"

        # Mask password in displayed command if it's a placeholder
        if vps_password == "<your-vps-password>":
            display_cmd = f'export DATABASE_URL="postgresql://{VPS_USER}:$VPS_DB_PASSWORD@{VPS_HOST}:{VPS_PORT}/{VPS_DB}"'
            description = "Set VPS_DB_PASSWORD env var first, then run this"
        else:
            display_cmd = f'export DATABASE_URL="{_mask_url(vps_url)}"'
            description = "Connect to VPS PostgreSQL via Tailscale"

        return {
            "mode": "vps",
            "command": display_cmd,
            "description": description,
        }
    else:
        return {
            "mode": "local",
            "command": "unset DATABASE_URL",
            "description": "Use local SQLite database",
        }
