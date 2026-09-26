"""Application settings, loaded once from the .env file at the repo root.

Using pydantic-settings rather than scattered os.getenv calls means every
setting is declared in one place, typed, and validated at startup - a missing
DATABASE_URL fails immediately with a clear error instead of at the first query.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/app -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # SQLAlchemy connection URL for the Postgres instance started by
    # `docker compose up -d` at the repo root.
    database_url: str = "postgresql+psycopg://taskflow:taskflow@localhost:5433/taskflow"

    # Optional. When empty, the AI suggestion feature is disabled and the rest
    # of the app runs normally, so the board works without an API key.
    anthropic_api_key: str = ""

    # Origins allowed to call this API. The Vite dev server runs on 5173.
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def ai_enabled(self) -> bool:
        """True when an API key is configured, so callers can degrade gracefully."""
        return bool(self.anthropic_api_key.strip())


settings = Settings()
