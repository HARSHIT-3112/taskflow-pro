"""Application settings, loaded once from the .env file at the repo root.

Using pydantic-settings rather than scattered os.getenv calls means every
setting is declared in one place, typed, and validated at startup - a missing
DATABASE_URL fails immediately with a clear error instead of at the first query.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/app -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # SQLAlchemy connection URL.
    #
    # Locally this is the Postgres started by `docker compose up -d`. Hosted
    # Postgres add-ons inject their own variable name instead of DATABASE_URL -
    # Supabase provides POSTGRES_URL_NON_POOLING and POSTGRES_URL - so those are
    # accepted as fallbacks and the deployment needs no manual wiring.
    #
    # The non-pooled URL is preferred: the pooled endpoint runs PgBouncer in
    # transaction mode, which does not support the prepared statements psycopg
    # uses by default.
    database_url: str = Field(
        default="postgresql+psycopg://taskflow:taskflow@localhost:5433/taskflow",
        validation_alias=AliasChoices(
            "DATABASE_URL",
            "POSTGRES_URL_NON_POOLING",
            "POSTGRES_URL",
        ),
    )

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

    # When the task table is empty at startup, create the demo project. Makes a
    # fresh deployment self-contained: no shell access needed to seed it.
    seed_if_empty: bool = True

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalise_database_url(cls, value: object) -> object:
        """Accept the URL shapes hosted Postgres providers hand out.

        Neon, Supabase, Render and Heroku all provide `postgres://` or
        `postgresql://` URLs. SQLAlchemy needs the driver named explicitly, so
        without this the deployed app fails at startup with an opaque dialect
        error - a classic works-locally-breaks-in-production trap.
        """
        if not isinstance(value, str) or not value:
            return value
        for prefix in ("postgresql+psycopg://", "postgresql+asyncpg://"):
            if value.startswith(prefix):
                return value
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_origins(cls, value: object) -> object:
        """Accept an empty value or a comma-separated list from .env.

        Without this, pydantic tries to JSON-decode the raw string, so the
        perfectly reasonable `CORS_ORIGINS=` in a .env file crashes startup.
        """
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return json.loads(stripped)
            return [part.strip() for part in stripped.split(",") if part.strip()]
        return value

    @property
    def ai_enabled(self) -> bool:
        """True when any model provider is configured."""
        return bool(self.gemini_api_key.strip() or self.anthropic_api_key.strip())


settings = Settings()
