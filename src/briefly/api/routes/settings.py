"""Settings management for Briefly 3000."""

import json
from fastapi import APIRouter
from pydantic import BaseModel
from pathlib import Path

from briefly.core.config import get_settings

router = APIRouter()

SETTINGS_FILE = Path(__file__).parent.parent.parent.parent.parent / ".cache" / "user_settings.json"


def _load_user_settings() -> dict:
    """Load user-configurable settings."""
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text())
    return {}


def _save_user_settings(settings: dict):
    """Save user settings."""
    SETTINGS_FILE.parent.mkdir(exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))


class UpdateSettingsRequest(BaseModel):
    summarization_model: str | None = None
    embedding_model: str | None = None
    hours_back_default: int | None = None


class LocalLLMRequest(BaseModel):
    enabled: bool
    base_url: str = "http://localhost:1234/v1"
    model: str = "local-model"
    api_key: str = "not-needed"


class ScheduleConfig(BaseModel):
    enabled: bool = False
    schedule_type: str = "daily"  # daily, interval
    time: str = "08:00"  # For daily - time in HH:MM
    interval_hours: int = 24  # For interval - hours between runs
    timezone: str = "America/Los_Angeles"
    category_ids: list[str] | None = None  # Which categories to include


class DeliveryConfig(BaseModel):
    dashboard: bool = True  # Always show on dashboard
    email: dict | None = None  # {"enabled": true, "address": "user@example.com"}
    telegram: dict | None = None  # {"enabled": true, "chat_id": "123456"}
    discord: dict | None = None  # {"enabled": true, "webhook_url": "..."}
    x_dm: dict | None = None  # {"enabled": true, "username": "@user"}
    x_post: dict | None = None  # {"enabled": true, "as_thread": true}


@router.get("")
async def get_all_settings() -> dict:
    """Get all settings including system info."""
    env_settings = get_settings()
    user_settings = _load_user_settings()

    # Get local LLM settings from user_settings (runtime configurable)
    local_llm = user_settings.get("local_llm", {})

    return {
        # Current LLM configuration
        "llm": {
            "summarization_model": user_settings.get("summarization_model", "grok-4.1-fast"),
            "summarization_provider": "local" if local_llm.get("enabled") else "xai",
            "embedding_model": "text-embedding-3-small",
            "embedding_provider": "openai",
            "embedding_dimensions": 1536,
        },
        # Local LLM configuration
        "local_llm": {
            "enabled": local_llm.get("enabled", env_settings.local_llm_enabled),
            "base_url": local_llm.get("base_url", env_settings.local_llm_base_url),
            "model": local_llm.get("model", env_settings.local_llm_model),
            "api_key_set": bool(local_llm.get("api_key") or env_settings.local_llm_api_key),
        },
        # API key status (not values, just whether they're set)
        "api_keys": {
            "x_api": bool(env_settings.x_api_key),
            "x_bearer": bool(env_settings.x_bearer_token),
            "youtube": bool(env_settings.youtube_api_key),
            "openai": bool(env_settings.openai_api_key),
            "xai": bool(env_settings.xai_api_key),
            "anthropic": bool(getattr(env_settings, 'anthropic_api_key', None)),
            "taddy": bool(env_settings.taddy_api_key),
        },
        # User preferences
        "preferences": {
            "hours_back_default": user_settings.get("hours_back_default", 72),
            "max_items_per_briefing": user_settings.get("max_items_per_briefing", 20),
        },
        # Available LLM options
        "available_models": {
            "summarization": [
                {"id": "grok-4.1-fast", "name": "Grok 4.1 Fast", "provider": "xai", "default": True},
                {"id": "grok-3", "name": "Grok 3", "provider": "xai"},
                {"id": "gpt-4o", "name": "GPT-4o", "provider": "openai"},
                {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "provider": "openai"},
                {"id": "claude-3-5-sonnet", "name": "Claude 3.5 Sonnet", "provider": "anthropic"},
                {"id": "claude-3-5-haiku", "name": "Claude 3.5 Haiku", "provider": "anthropic"},
            ],
            "embedding": [
                {"id": "text-embedding-3-small", "name": "OpenAI Small", "provider": "openai", "dimensions": 1536, "default": True},
                {"id": "text-embedding-3-large", "name": "OpenAI Large", "provider": "openai", "dimensions": 3072},
            ],
        },
        # Feature flags
        "features": {
            "semantic_search": True,
            "transcript_summarization": True,
            "categories": True,
            "podcasts": bool(env_settings.taddy_api_key),  # Enabled if Taddy configured
            "local_transcription": _check_local_transcription(),
            "rss_feeds": False,  # Coming soon
            "scheduler": True,
            "delivery": True,
        },
        # Scheduler configuration
        "scheduler": user_settings.get("scheduler", {
            "enabled": False,
            "schedule_type": "daily",
            "time": "08:00",
            "interval_hours": 24,
            "timezone": "America/Los_Angeles",
            "category_ids": None,
            "last_run": None,
            "next_run": None,
        }),
        # Delivery configuration
        "delivery": user_settings.get("delivery", {
            "dashboard": True,
            "email": None,
            "telegram": None,
            "discord": None,
            "x_dm": None,
            "x_post": None,
        }),
        # Transcription settings
        "transcription": {
            "local_available": _check_local_transcription(),
            "default_model": "distil-medium.en",
            "models": _get_transcription_models(),
        },
    }


def _check_local_transcription() -> bool:
    """Check if local transcription is available."""
    try:
        import mlx_whisper
        return True
    except ImportError:
        return False


def _get_transcription_models() -> list:
    """Get available transcription models."""
    try:
        from briefly.services.transcription import WHISPER_MODELS
        return [{"id": k, **v} for k, v in WHISPER_MODELS.items()]
    except ImportError:
        return []


@router.put("")
async def update_settings(req: UpdateSettingsRequest) -> dict:
    """Update user settings."""
    settings = _load_user_settings()

    if req.summarization_model is not None:
        settings["summarization_model"] = req.summarization_model
    if req.embedding_model is not None:
        settings["embedding_model"] = req.embedding_model
    if req.hours_back_default is not None:
        settings["hours_back_default"] = req.hours_back_default

    _save_user_settings(settings)

    return {"status": "updated", "settings": settings}


@router.get("/health")
async def settings_health() -> dict:
    """Quick health check for required configurations."""
    env = get_settings()
    user_settings = _load_user_settings()
    local_llm = user_settings.get("local_llm", {})

    issues = []

    # Only warn about xAI key if local LLM is not enabled
    if not local_llm.get("enabled") and not env.xai_api_key:
        issues.append("XAI_API_KEY not set - summarization will fail (or enable local LLM)")
    if not env.openai_api_key:
        issues.append("OPENAI_API_KEY not set - embeddings and search will fail")
    if not env.youtube_api_key:
        issues.append("YOUTUBE_API_KEY not set - YouTube sources won't work")
    if not env.x_bearer_token:
        issues.append("X_BEARER_TOKEN not set - X sources won't work")

    return {
        "healthy": len(issues) == 0,
        "issues": issues,
    }


@router.put("/local-llm")
async def update_local_llm(req: LocalLLMRequest) -> dict:
    """Update local LLM configuration."""
    settings = _load_user_settings()

    settings["local_llm"] = {
        "enabled": req.enabled,
        "base_url": req.base_url,
        "model": req.model,
        "api_key": req.api_key,
    }

    _save_user_settings(settings)

    # Clear the settings cache so new SummarizationService instances use updated config
    from briefly.core.config import get_settings
    get_settings.cache_clear()

    return {
        "status": "updated",
        "local_llm": {
            "enabled": req.enabled,
            "base_url": req.base_url,
            "model": req.model,
        },
        "message": "Restart may be required for changes to take full effect" if req.enabled else "Using cloud LLM",
    }


@router.put("/scheduler")
async def update_scheduler(req: ScheduleConfig) -> dict:
    """Update scheduler configuration."""
    settings = _load_user_settings()

    settings["scheduler"] = {
        "enabled": req.enabled,
        "schedule_type": req.schedule_type,
        "time": req.time,
        "interval_hours": req.interval_hours,
        "timezone": req.timezone,
        "category_ids": req.category_ids,
    }

    _save_user_settings(settings)

    # TODO: Actually update the scheduler if it's running
    # This will require integrating with APScheduler or similar

    return {
        "status": "updated",
        "scheduler": settings["scheduler"],
        "message": "Scheduler updated. Restart server to apply changes." if req.enabled else "Scheduler disabled.",
    }


@router.put("/delivery")
async def update_delivery(req: DeliveryConfig) -> dict:
    """Update delivery configuration."""
    settings = _load_user_settings()

    settings["delivery"] = {
        "dashboard": req.dashboard,
        "email": req.email,
        "telegram": req.telegram,
        "discord": req.discord,
        "x_dm": req.x_dm,
        "x_post": req.x_post,
    }

    _save_user_settings(settings)

    return {
        "status": "updated",
        "delivery": settings["delivery"],
    }


@router.post("/delivery/test/{channel}")
async def test_delivery(channel: str, message: str = "Test briefing from Briefly 3000!") -> dict:
    """Test a delivery channel."""
    settings = _load_user_settings()
    delivery = settings.get("delivery", {})

    if channel == "telegram":
        config = delivery.get("telegram")
        if not config or not config.get("enabled"):
            return {"success": False, "error": "Telegram not configured"}

        try:
            import httpx
            bot_token = config.get("bot_token")
            chat_id = config.get("chat_id")

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    return {"success": True, "message": "Telegram message sent!"}
                else:
                    return {"success": False, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif channel == "discord":
        config = delivery.get("discord")
        if not config or not config.get("enabled"):
            return {"success": False, "error": "Discord not configured"}

        try:
            import httpx
            webhook_url = config.get("webhook_url")

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    webhook_url,
                    json={"content": message},
                    timeout=10.0,
                )
                if resp.status_code in [200, 204]:
                    return {"success": True, "message": "Discord message sent!"}
                else:
                    return {"success": False, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    else:
        return {"success": False, "error": f"Unknown channel: {channel}"}


@router.post("/local-llm/test")
async def test_local_llm(req: LocalLLMRequest) -> dict:
    """Test connection to local LLM server."""
    import httpx
    import traceback

    # First, do a quick connectivity check (faster than waiting for OpenAI timeout)
    try:
        async with httpx.AsyncClient() as client:
            # Just check if the server is responding at all
            health_url = req.base_url.rstrip('/').replace('/v1', '') + '/health'
            models_url = req.base_url.rstrip('/') + '/models'

            try:
                # Try models endpoint first (more standard)
                resp = await client.get(models_url, timeout=3.0)
                if resp.status_code not in [200, 401, 403]:
                    # Try base URL
                    base_resp = await client.get(req.base_url.rstrip('/'), timeout=3.0)
            except httpx.ConnectError:
                return {
                    "success": False,
                    "error": f"Connection refused - no server at {req.base_url}. Is LM Studio or Ollama running?",
                }
            except httpx.ConnectTimeout:
                return {
                    "success": False,
                    "error": f"Connection timeout - server not responding at {req.base_url}",
                }
    except Exception as e:
        return {
            "success": False,
            "error": f"Connection check failed: {str(e)}",
        }

    # Server is reachable, now test actual completion
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=req.api_key,
            base_url=req.base_url,
            timeout=10.0,
        )

        response = await client.chat.completions.create(
            model=req.model,
            messages=[{"role": "user", "content": "Say 'hello' in one word."}],
            max_tokens=10,
        )

        return {
            "success": True,
            "response": response.choices[0].message.content,
            "model": response.model,
        }
    except Exception as e:
        exc_type = type(e).__name__
        exc_str = str(e)
        tb = traceback.format_exc()

        error_parts = [f"[{exc_type}]"]
        if exc_str and exc_str != exc_type:
            error_parts.append(exc_str[:200])

        if "model" in exc_str.lower() and "not found" in exc_str.lower():
            error_parts.append(f"Model '{req.model}' not found - check available models in LM Studio")

        return {
            "success": False,
            "error": " ".join(error_parts),
            "details": tb[:500],
        }
