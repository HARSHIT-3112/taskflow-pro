"""FastAPI application entry point.

Run locally with:
    uvicorn app.main:app --reload --port 8000

Interactive API docs are then at http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import dependencies as dependencies_routes
from app.api import suggestions as suggestions_routes
from app.api import tasks as tasks_routes
from app.config import settings
from app.db import create_db_and_tables


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create any missing tables, and seed an empty database, at startup.

    Seeding only happens when the task table is completely empty, so it never
    touches a board someone is using. It makes a fresh deployment
    self-contained: no shell access is needed to get a demo board.
    """
    create_db_and_tables()

    if settings.seed_if_empty:
        from sqlmodel import Session, select

        from app.db import engine
        from app.models import Task

        try:
            with Session(engine) as session:
                if session.exec(select(Task)).first() is None:
                    from seed import seed

                    seed()
        except Exception:
            # A seeding failure must never stop the API from serving.
            logging.getLogger(__name__).exception("Could not seed an empty database")

    yield


app = FastAPI(
    title="TaskFlow Pro",
    description=(
        "Dependency-aware Kanban board. A DAG engine decides which tasks are "
        "ready, which are blocked, and how a schedule change cascades."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# The React dev server runs on a different port, so the browser treats it as a
# different origin and requires an explicit allow.
#
# With CORS_ORIGINS set (a deployment), only those exact origins are accepted.
# With it unset (local development), any loopback port is accepted - never "*",
# which would expose this API to every site on the internet.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=settings.cors_localhost_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(tasks_routes.router)
app.include_router(dependencies_routes.router)
app.include_router(suggestions_routes.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, object]:
    """Liveness check, and whether the optional AI feature is configured."""
    return {"status": "ok", "ai_enabled": settings.ai_enabled}
