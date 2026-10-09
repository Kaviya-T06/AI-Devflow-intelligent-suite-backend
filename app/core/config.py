"""
Application configuration — reads from environment variables / .env file.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central settings object.  All values come from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Supabase / PostgreSQL
    # ------------------------------------------------------------------
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    DATABASE_URL: str = ""

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    APP_SECRET_KEY: str = "change-me"
    
    # ------------------------------------------------------------------
    # AI / GitHub Integration
    # ------------------------------------------------------------------
    AI_API_KEY: str = ""
    AI_MODEL: str = "gemini-3.8-flash"
    AI_PROVIDER: str = "google-gemini"
    GITHUB_TOKEN: str = ""

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------
    FRONTEND_URL: str = "http://localhost:5173"

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    API_V1_PREFIX: str = "/api/v1"


# Singleton — import this everywhere
settings = Settings()
