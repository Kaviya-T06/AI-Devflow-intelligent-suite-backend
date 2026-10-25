"""
Application configuration — reads from environment variables / .env file.
"""
import os
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
    # Role Authorization / Invitation Keys
    # ------------------------------------------------------------------
    ADMIN_INVITE_CODE: str = "DEVFLOW-ADMIN-SECURE-KEY"
    MANAGER_INVITE_CODE: str = "DEVFLOW-PM-SECURE-KEY"
    INVITATION_CODE: str = ""

    # ------------------------------------------------------------------
    # AI / Ollama Integration
    # ------------------------------------------------------------------
    AI_API_KEY: str = ""
    AI_MODEL: str = "gemini-3.8-flash"
    AI_PROVIDER: str = "google-gemini"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2:1b"
    OLLAMA_TIMEOUT: float = 60.0
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

_PLACEHOLDER_TOKENS = {
    "your-github-token-here",
    "your_github_token",
    "your-token-here",
    "your_token",
    "placeholder",
    "xxx",
    "none",
    "<token>",
}


def get_github_token() -> str:
    """
    Returns active GitHub token if configured and valid (non-placeholder).
    Checks settings.GITHUB_TOKEN first, then environment variables GITHUB_TOKEN and GH_TOKEN.
    Returns empty string if no valid token is available.
    """
    token = getattr(settings, "GITHUB_TOKEN", "")
    if token and isinstance(token, str):
        cleaned = token.strip()
        if cleaned.lower() not in _PLACEHOLDER_TOKENS:
            return cleaned

    # Fallback to system environment variables
    env_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    cleaned_env = env_token.strip()
    if cleaned_env and cleaned_env.lower() not in _PLACEHOLDER_TOKENS:
        return cleaned_env

    return ""
