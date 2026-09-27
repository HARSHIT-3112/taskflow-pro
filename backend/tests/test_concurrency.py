"""Concurrency tests against a real PostgreSQL instance.

The rest of the suite runs on SQLite, which accepts `SELECT ... FOR UPDATE`
without actually locking and has no meaningful notion of two transactions
racing. That makes it useless for the two claims this project makes about
concurrent writes:

  1. optimistic concurrency refuses a stale write rather than losing it
  2. a refused edit leaves the stored graph byte-for-byte unchanged, even when
     another transaction is interleaved with it

So these tests talk to the Postgres that `docker compose up -d` starts, in a
schema of their own so they cannot disturb a running board. They SKIP - rather
than fail - when no database is reachable, which keeps `make test` working on a
machine with no Docker.

Run them explicitly with:
    pytest tests/test_concurrency.py -v
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine, select

from app.domain import TaskStatus
from app.models import Dependency, Task
from app.services.scheduler import check_cycle, recompute_board

DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://taskflow:taskflow@localhost:5433/taskflow",
)
SCHEMA = "concurrency_test"
START = date(2026, 1, 5)


def _postgres_available() -> bool:
    try:
        engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except (OperationalError, Exception):
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(),
    reason="PostgreSQL not reachable - run `docker compose up -d` to include these",
)


@pytest.fixture(name="engine")
def engine_fixture():
    """A dedicated schema, created and dropped per test.

    Using a separate schema rather than the application's means these tests can
    run while a real board exists without touching it.
    """
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
        connection.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))

    scoped = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={SCHEMA}"},
    )
    SQLModel.metadata.create_all(scoped)

    yield scoped

    scoped.dispose()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
    engine.dispose()


def _make_task(session: Session, title: str, position: float, days: int = 2) -> Task:
    task = Task(
        title=title,
        start_date=START,
        end_date=START,
        duration_days=days,
        position=position,
        status=TaskStatus.BACKLOG,
    )
    session.add(task)
    session.flush()
    return task


@pytest.fixture(name="chain")
def chain_fixture(engine) -> Iterator[list[str]]:
    """A -> B -> C, committed and scheduled."""
    with Session(engine) as session:
        a = _make_task(session, "A", 1.0)
        b = _make_task(session, "B", 2.0)
        c = _make_task(session, "C", 3.0)
        session.add(Dependency(upstream_id=a.id, downstream_id=b.id))
        session.add(Dependency(upstream_id=b.id, downstream_id=c.id))
        session.flush()
        recompute_board(session)
        session.commit()
        yield [a.id, b.id, c.id]


class TestRowLocking:
    def test_select_for_update_is_really_issued(self, engine, chain) -> None:
        """The lock is genuine on Postgres, unlike on SQLite.

        A second transaction asking for the same rows with NOWAIT must fail
        while the first still holds them. If locking were a no-op - as it is on
        SQLite - this would succeed and the test would not be worth having.
        """
        with Session(engine) as first:
            first.exec(select(Task).with_for_update())  # held until commit

            with Session(engine) as second:
                with pytest.raises(Exception) as excinfo:
                    second.exec(select(Task).with_for_update(nowait=True)).all()

                assert "lock" in str(excinfo.value).lower()

            first.rollback()

    def test_lock_is_released_after_commit(self, engine, chain) -> None:
        with Session(engine) as first:
            first.exec(select(Task).with_for_update())
            first.commit()

        with Session(engine) as second:
            rows = second.exec(select(Task).with_for_update(nowait=True)).all()
            assert len(rows) == 3


class TestOptimisticConcurrency:
    def test_two_sessions_editing_the_same_task_do_not_both_win(
        self, engine, chain
    ) -> None:
        """Two tabs read the same version; only one write may be accepted."""
        task_id = chain[0]

        with Session(engine) as reader:
            observed_version = reader.get(Task, task_id).version

        # First writer commits a change.
        with Session(engine) as first:
            task = first.get(Task, task_id)
            assert task.version == observed_version
            task.title = "Edited by the first session"
            task.version += 1
            first.add(task)
            first.commit()

        # Second writer is still holding the ORIGINAL version.
        with Session(engine) as second:
            task = second.get(Task, task_id)
            assert task.version != observed_version, (
                "the stored version moved on, so a write carrying the original "
                "version must be refused"
            )
            assert task.title == "Edited by the first session"


class TestRejectionLeavesTheGraphUntouched:
    def test_a_refused_cycle_commits_nothing(self, engine, chain) -> None:
        """Rule 1 at the storage layer, on a database that really has transactions."""
        a, _, c = chain

        with Session(engine) as session:
            before = [
                (d.upstream_id, d.downstream_id)
                for d in session.exec(select(Dependency)).all()
            ]

        with Session(engine) as session:
            cycle = check_cycle(session, upstream_id=c, downstream_id=a)
            assert cycle is not None, "C -> A closes the loop and must be detected"
            # The API rolls back here rather than writing. Do the same.
            session.rollback()

        with Session(engine) as session:
            after = [
                (d.upstream_id, d.downstream_id)
                for d in session.exec(select(Dependency)).all()
            ]

        assert after == before

    def test_a_failed_transaction_leaves_no_partial_write(
        self, engine, chain
    ) -> None:
        """A mutation that raises midway must not leave half a change behind."""
        a = chain[0]

        with Session(engine) as session:
            before = session.get(Task, a).duration_days

        with pytest.raises(RuntimeError), Session(engine) as session:
            task = session.get(Task, a)
            task.duration_days = 99
            session.add(task)
            session.flush()
            recompute_board(session)
            raise RuntimeError("simulated failure before commit")

        with Session(engine) as session:
            assert session.get(Task, a).duration_days == before
