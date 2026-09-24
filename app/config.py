"""Application settings loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. See `.env.example` for descriptions."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str | None = None
    vapi_secret: str | None = None
    log_level: str = "INFO"
    seed_demo_data: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
