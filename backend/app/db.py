"""Database engine and session management.

One SQLAlchemy engine is created for the whole process (it owns the connection
pool). Each request gets its own short-lived Session from that pool.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

# `pool_pre_ping` sends a cheap SELECT 1 before handing out a pooled connection,
# so a connection dropped by a Postgres restart is replaced instead of raising.
#
# On a serverless host each invocation may run in its own short-lived process,
# so holding a pool there leaks connections until the database refuses new ones.
# NullPool opens and closes per request instead, which is the right trade when
# the process itself is ephemeral.
_serverless = bool(os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"))

engine = create_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    **({"poolclass": NullPool} if _serverless else {}),
)


def create_db_and_tables() -> None:
    """Create any missing tables from the SQLModel definitions.

    Called once at application startup. This project has no migration tool;
    see docs/KNOWN-FAILURES.md for why, and what production would use instead.
    """
    # Importing models registers them on SQLModel.metadata before create_all.
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped database session.

    The `with` block guarantees the session is closed and its connection
    returned to the pool even if the request handler raises.
    """
    with Session(engine) as session:
        yield session
