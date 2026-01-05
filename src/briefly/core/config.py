"""Application configuration loaded from environment."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_env: str = "development"
    debug: bool = True
    secret_key: str = "dev-secret-key"

    # X API (Bot account @briefly3000)
    x_username: str = "briefly3000"
    x_api_key: str
    x_api_key_secret: str
    x_bearer_token: str
    x_access_token: str
    x_access_token_secret: str

    # xAI (Grok)
    xai_api_key: str
    xai_base_url: str = "https://api.x.ai/v1"
    xai_model: str = "grok-4-1-fast"  # Main model for briefings
    xai_model_cheap: str = "grok-4-1-fast"  # TODO: investigate cheaper providers (Gemini Flash, Claude Haiku, etc.)

    # Database
    database_url: str

    # Redis
    redis_url: str = "redis://localhost:6382/0"

    # n8n Integration
    n8n_base_url: str = "http://localhost:5678"
    n8n_webhook_path: str = "/webhook/briefing"
    n8n_api_key: str | None = None  # Optional API key for n8n

    # X Lists feature flag (use persistent list for efficient fetching)
    use_x_lists: bool = True

    # YouTube (optional)
    youtube_api_key: str | None = None

    # OpenAI (for embeddings)
    openai_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # Chunking settings
    chunk_size_tokens: int = 500
    chunk_overlap_tokens: int = 50

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
