"""Database engine and session management.

One SQLAlchemy engine is created for the whole process (it owns the connection
pool). Each request gets its own short-lived Session from that pool.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

# `pool_pre_ping` sends a cheap SELECT 1 before handing out a pooled connection,
# so a connection dropped by a Postgres restart is replaced instead of raising.
engine = create_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
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
