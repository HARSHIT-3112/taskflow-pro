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

    # Optional model providers for the AI suggestion feature. When neither is
    # set, suggestions are disabled and the rest of the app runs normally, so
    # the board works without any API key at all.
    #
    # Gemini is preferred when both are present: its free tier makes the
    # feature reproducible for anyone running this project.
    gemini_api_key: str = ""
    anthropic_api_key: str = ""

    # Origins allowed to call this API.
    #
    # Deliberately NOT "*": a wildcard would let any site on the internet call
    # this API from a visitor's browser. Instead any port on loopback is
    # allowed, because Vite picks 5173 but steps to the next free port when
    # something else holds it - and hard-coding a short list meant the board
    # failed with an opaque CORS error on a machine where those ports were busy.
    #
    # For a deployment, set CORS_ORIGINS in .env to the real site origin; when
    # it is set, only those origins are allowed and the loopback rule is off.
    cors_origins: list[str] = []

    cors_localhost_regex: str = r"http://(localhost|127\.0\.0\.1)(:\d+)?"

    @property
    def ai_enabled(self) -> bool:
        """True when any model provider is configured."""
        return bool(self.gemini_api_key.strip() or self.anthropic_api_key.strip())


settings = Settings()
