"""Environment-based application configuration (pydantic-settings)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env, resolved from this file so the CWD the server is launched from
# doesn't matter (config.py lives at backend/app/config.py).
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """Settings loaded from ``backend/.env`` (and process environment)."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # asyncpg URL, e.g. postgresql+asyncpg://user:pass@host:5432/db
    # Required — no default, so a missing DATABASE_URL fails at boot instead of
    # silently connecting to the wrong database.
    database_url: str


def get_settings() -> Settings:
    """Return the application settings."""
    return Settings()


settings = get_settings()
