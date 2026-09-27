"""Serverless entry point for the API.

Vercel's Python runtime is file-based: this module is mounted at exactly
`/api/index` and nothing else. So vercel.json rewrites every `/api/...`
request to `/api/index` and carries the requested path in a `__path` query
parameter, which Vercel does forward. The ASGI wrapper below puts that path
back before the request reaches FastAPI.

The shim lives here, in the deployment entry point, rather than in the
application: app/main.py stays a plain FastAPI app that runs identically under
`uvicorn app.main:app` locally and knows nothing about Vercel.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app as fastapi_app  # noqa: E402

# Query parameter carrying the real request path. Named with a double
# underscore so it cannot collide with a genuine API parameter.
_PATH_PARAM = "__path"


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Restore the original request path, then delegate to FastAPI."""
    if scope.get("type") == "http":
        raw_query: bytes = scope.get("query_string", b"") or b""
        params = parse_qsl(raw_query.decode("latin-1"), keep_blank_values=True)

        original = next((value for key, value in params if key == _PATH_PARAM), None)
        if original is not None:
            remaining = [(k, v) for k, v in params if k != _PATH_PARAM]
            scope = {
                **scope,
                "path": "/api/" + original.lstrip("/"),
                "raw_path": None,
                "query_string": urlencode(remaining).encode("latin-1"),
            }

    await fastapi_app(scope, receive, send)
