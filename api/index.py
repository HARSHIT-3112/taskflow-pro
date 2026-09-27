"""Serverless entry point for the API.

Vercel runs this module as a Python function and routes every /api/* request to
it (see vercel.json). The FastAPI app is imported unchanged - nothing about the
application knows it is running serverless.

The backend/ directory is added to the import path because the application is
laid out for a normal `uvicorn app.main:app` run from inside backend/, and that
should stay true for local development.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402

# Vercel's Python runtime looks for a module-level ASGI callable named `app`.
__all__ = ["app"]
