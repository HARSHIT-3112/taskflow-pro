"""Shared test fixtures.

The API tests run against an in-memory SQLite database rather than Postgres, so
`make test` works on a machine with no Docker running. What is under test here
is the API layer's own behaviour - status codes, validation, transaction
boundaries, optimistic concurrency and the shape of the cascade - none of which
depend on the storage engine.

Foreign-key enforcement is switched on explicitly below, because SQLite ignores
it by default while Postgres does not - without that, ON DELETE CASCADE would
silently not happen and a test would pass for the wrong reason.

The two things SQLite still cannot exercise are `SELECT ... FOR UPDATE` row
locking (accepted but a no-op) and genuine concurrent transactions. That gap is
recorded in docs/KNOWN-FAILURES.md rather than papered over.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app

START = date(2026, 1, 5)


@pytest.fixture(name="session")
def session_fixture() -> Iterator[Session]:
    """A fresh, empty database for every test.

    StaticPool keeps one connection alive so the in-memory database survives
    across the requests made within a single test.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite ignores foreign keys unless asked not to. Postgres enforces
    # ON DELETE CASCADE on the dependency table, so turning this on is what
    # makes the test database behave like the real one.
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session) -> Iterator[TestClient]:
    """A TestClient wired to the per-test database.

    Overriding the `get_session` dependency is what redirects the app away from
    Postgres - the application code is untouched.
    """

    def override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_task(
    client: TestClient,
    title: str,
    *,
    days: int = 1,
    start: date = START,
    status: str = "BACKLOG",
) -> dict:
    """Create a task and return it, failing loudly if creation did not work."""
    response = client.post(
        "/api/tasks",
        json={
            "title": title,
            "start_date": start.isoformat(),
            "duration_days": days,
            "status": status,
        },
    )
    assert response.status_code == 201, response.text
    created = [t for t in response.json()["affected"] if t["title"] == title]
    assert created, f"created task {title!r} was not returned in `affected`"
    return created[0]


def link(client: TestClient, upstream: dict, downstream: dict) -> dict:
    """Add a dependency, failing loudly if it was rejected."""
    response = client.post(
        "/api/dependencies",
        json={"upstream_id": upstream["id"], "downstream_id": downstream["id"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def board(client: TestClient) -> dict[str, dict]:
    """The whole board, keyed by title for readable assertions."""
    response = client.get("/api/board")
    assert response.status_code == 200
    return {t["title"]: t for t in response.json()["tasks"]}


def raw_board(client: TestClient) -> dict:
    return client.get("/api/board").json()


@pytest.fixture(name="diamond")
def diamond_fixture(client: TestClient) -> dict[str, dict]:
    """The graph from the problem statement.

        A ─┬─> B ─┬─> D
           └─> C ─┘
    """
    a = make_task(client, "A", days=3)
    b = make_task(client, "B", days=2)
    c = make_task(client, "C", days=2)
    d = make_task(client, "D", days=2)

    link(client, a, b)
    link(client, a, c)
    link(client, b, d)
    link(client, c, d)

    return board(client)
