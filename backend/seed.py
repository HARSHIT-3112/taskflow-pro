"""Seed the board with a realistic software-delivery project.

Run with:
    python seed.py

The graph is deliberately shaped so every rule in the problem statement can be
demonstrated without setting anything up first:

    Design schema ─┬─> Build REST API ──────┬─> Integration tests ─┬─> Deploy ─┬─> Load testing ───┬─> Release
                   └─> Build auth service ──┘                      │           └─> Security review ┘
                                                                   │
                                          Set up CI pipeline ──────┘

  * DIAMOND 1   schema -> {REST API, auth} -> integration tests
  * DIAMOND 2   deploy -> {load testing, security review} -> release
                Extending an upstream task must move the join point ONCE.

  * DEEP CHAIN  schema -> API -> integration -> deploy -> load -> release
                Six levels, so propagation depth is visible.

  * CYCLE DEMO  try adding "Production release" -> "Design schema".
                It closes a loop and must be refused, naming the path.

  * ROLLBACK    "Design schema" starts DONE, so its dependents are READY.
                Drag it back to In Progress and they must become BLOCKED.

Titles and descriptions are written to be genuinely informative, because the AI
suggestion feature infers dependencies from exactly this text.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

from sqlmodel import Session, delete, select

from app.db import create_db_and_tables, engine
from app.domain import DependencyOrigin, TaskStatus
from app.models import Dependency, Suggestion, Task
from app.services.scheduler import recompute_board

# Anchor the project to the coming Monday so seeded dates always look current.
TODAY = date.today()
PROJECT_START = TODAY - timedelta(days=TODAY.weekday())


# (key, title, description, duration_days, status)
TASKS: list[tuple[str, str, str, int, TaskStatus]] = [
    (
        "schema",
        "Design database schema",
        "Define tables, relationships and indexes for tasks, dependencies and "
        "users. Produces the migration that every other backend task builds on.",
        3,
        TaskStatus.DONE,
    ),
    (
        "api",
        "Build REST API endpoints",
        "Implement CRUD endpoints for tasks and dependencies against the agreed "
        "database schema, including request validation and error responses.",
        4,
        TaskStatus.IN_PROGRESS,
    ),
    (
        "auth",
        "Build authentication service",
        "Session handling, login and token refresh. Reads the users table "
        "defined by the database schema.",
        3,
        TaskStatus.IN_PROGRESS,
    ),
    (
        "integration",
        "Write integration tests",
        "End-to-end tests covering authenticated API calls. Cannot run until "
        "both the REST endpoints and the authentication service are working.",
        3,
        TaskStatus.BACKLOG,
    ),
    (
        "ci",
        "Set up CI pipeline",
        "GitHub Actions workflow running lint and the test suite on every push. "
        "Independent of application code, so it can start immediately.",
        2,
        TaskStatus.REVIEW,
    ),
    (
        "frontend",
        "Build Kanban board UI",
        "React board with drag-and-drop between columns. Consumes the REST API, "
        "so the endpoint contract must be settled first.",
        5,
        TaskStatus.BACKLOG,
    ),
    (
        "deploy",
        "Deploy to staging environment",
        "Provision the staging environment and run the first deployment. "
        "Requires a green integration suite and a working CI pipeline.",
        1,
        TaskStatus.BACKLOG,
    ),
    (
        "load",
        "Run load testing",
        "Measure p95 latency and throughput against staging under simulated "
        "concurrent users.",
        2,
        TaskStatus.BACKLOG,
    ),
    (
        "security",
        "Complete security review",
        "Check authentication flows, dependency vulnerabilities and secret "
        "handling on the deployed staging environment.",
        2,
        TaskStatus.BACKLOG,
    ),
    (
        "release",
        "Production release",
        "Promote the validated staging build to production. Blocked until both "
        "load testing and the security review have signed off.",
        1,
        TaskStatus.BACKLOG,
    ),
]

# (upstream key, downstream key)
EDGES: list[tuple[str, str]] = [
    ("schema", "api"),          # diamond 1 opens
    ("schema", "auth"),
    ("api", "integration"),     # diamond 1 closes
    ("auth", "integration"),
    ("api", "frontend"),
    ("integration", "deploy"),
    ("ci", "deploy"),
    ("deploy", "load"),         # diamond 2 opens
    ("deploy", "security"),
    ("load", "release"),        # diamond 2 closes
    ("security", "release"),
]


def seed() -> None:
    create_db_and_tables()

    with Session(engine) as session:
        # Start from a clean board so the script is safe to re-run.
        session.exec(delete(Suggestion))
        session.exec(delete(Dependency))
        session.exec(delete(Task))
        session.commit()

        ids: dict[str, str] = {}
        for position, (key, title, description, days, status) in enumerate(TASKS, start=1):
            task = Task(
                title=title,
                description=description,
                status=status,
                # Every task starts at the project start; the engine pushes each
                # one to its real date based on the dependency graph.
                start_date=PROJECT_START,
                end_date=PROJECT_START,
                duration_days=days,
                position=float(position),
            )
            session.add(task)
            session.flush()
            ids[key] = task.id

        for upstream_key, downstream_key in EDGES:
            session.add(
                Dependency(
                    upstream_id=ids[upstream_key],
                    downstream_id=ids[downstream_key],
                    origin=DependencyOrigin.HUMAN,
                )
            )

        session.flush()

        # Let the engine assign every real date and blocked/ready value, exactly
        # as it would after any user edit. Nothing here hand-writes a schedule.
        recompute_board(session)
        session.commit()

        _report(session, ids)


def _report(session: Session, ids: dict[str, str]) -> None:
    """Print the seeded board and what a reviewer should try."""
    tasks = {t.id: t for t in session.exec(select(Task)).all()}
    by_key = {key: tasks[task_id] for key, task_id in ids.items()}

    print(f"\nSeeded {len(tasks)} tasks, {len(EDGES)} dependencies.")
    print(f"Project start: {PROJECT_START}\n")

    width = max(len(t.title) for t in tasks.values())
    print(f"  {'TASK'.ljust(width)}  {'STATUS':<12} {'STATE':<8} DATES")
    print(f"  {'-' * width}  {'-' * 12} {'-' * 8} {'-' * 23}")
    for key, _, _, _, _ in TASKS:
        task = by_key[key]
        print(
            f"  {task.title.ljust(width)}  {task.status.value:<12} "
            f"{task.dependency_state.value:<8} {task.start_date} -> {task.end_date}"
        )

    print("\nTry these to see the four rules:")
    print("  1. NO CYCLES      Add a dependency: 'Production release' -> 'Design database schema'.")
    print("                    It must be refused, naming the circular path.")
    print("  2. NO COMPOUNDING Extend 'Design database schema' by 3 days.")
    print("                    'Write integration tests' must move 3 days, not 6,")
    print("                    even though two paths reach it.")
    print("  3. ROLLBACK       Drag 'Design database schema' from Done to In Progress.")
    print("                    Its dependents must turn BLOCKED.")
    print("  4. PERSISTENCE    Refresh the browser. Everything stays put.\n")


if __name__ == "__main__":
    try:
        seed()
    except Exception as exc:  # pragma: no cover - operator feedback only
        print(f"Seed failed: {exc}", file=sys.stderr)
        print("Is Postgres running? Try: docker compose up -d", file=sys.stderr)
        raise SystemExit(1) from exc
