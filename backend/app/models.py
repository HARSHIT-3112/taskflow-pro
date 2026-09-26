"""Database tables for TaskFlow Pro.

The dependency graph lives in the Dependency table: one row per directed edge.
Only the engine (app/engine) may write `start_date`, `end_date` and
`dependency_state`. Nothing else sets them by hand.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _new_id() -> str:
    """Generate a short unique id. Used as the primary key for every table."""
    return uuid4().hex


def _utcnow() -> datetime:
    """Timezone-aware creation timestamp, so ordering is unambiguous."""
    return datetime.now(timezone.utc)


class TaskStatus(str, enum.Enum):
    """The four Kanban columns.

    This is workflow stage only. It says nothing about whether a task is
    blocked - that is DependencyState, which is computed from the graph.
    A BACKLOG task can be READY and an IN_PROGRESS task can be BLOCKED.
    """

    BACKLOG = "BACKLOG"
    IN_PROGRESS = "IN_PROGRESS"
    REVIEW = "REVIEW"
    DONE = "DONE"


class DependencyState(str, enum.Enum):
    """Derived from the graph on every recompute. Never set by hand.

    READY   - every prerequisite is DONE
    BLOCKED - at least one prerequisite is not DONE
    """

    READY = "READY"
    BLOCKED = "BLOCKED"


class DependencyOrigin(str, enum.Enum):
    """Who created an edge.

    Keeps the graph auditable and lets us measure how often AI suggestions
    survive human review.
    """

    HUMAN = "HUMAN"
    AI_ACCEPTED = "AI_ACCEPTED"


class SuggestionStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class Task(SQLModel, table=True):
    """A card on the board."""

    id: str = Field(default_factory=_new_id, primary_key=True)
    title: str
    description: str = ""
    status: TaskStatus = Field(default=TaskStatus.BACKLOG, index=True)

    # duration_days is the source of truth. end_date is always
    # start_date + duration_days - 1, written by the engine, so the two can
    # never disagree. A 1-day task starts and ends on the same day.
    start_date: date
    end_date: date
    duration_days: int = Field(default=1, ge=1)

    # When True the engine treats start_date as a floor and will not schedule
    # this task any earlier, even if its prerequisites would allow it.
    pinned: bool = False

    # Fractional rank within a column. Dropping a card between two others takes
    # the midpoint, so one row is written instead of renumbering the column.
    position: float = 0.0

    # Denormalised cache of the engine's verdict, so the board can render
    # without recomputing. Never an input to any decision: deleting this column
    # and recomputing from the graph would give identical answers.
    dependency_state: DependencyState = DependencyState.READY

    # Optimistic concurrency. Every write increments this; a write carrying a
    # stale version is rejected rather than overwriting a newer plan.
    version: int = 0

    # Which prerequisite actually determined this task's start_date, so the UI
    # can answer "why is this task on this date?" with the real reason.
    binding_constraint_id: str | None = None

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Dependency(SQLModel, table=True):
    """One directed edge: upstream must finish before downstream can start."""

    __table_args__ = (
        # The same edge cannot be added twice.
        UniqueConstraint("upstream_id", "downstream_id", name="uq_dependency_edge"),
    )

    id: str = Field(default_factory=_new_id, primary_key=True)

    # Both ends are indexed because we walk the graph in both directions:
    # forwards ("what depends on this?") and backwards ("what does this wait on?").
    upstream_id: str = Field(foreign_key="task.id", index=True, ondelete="CASCADE")
    downstream_id: str = Field(foreign_key="task.id", index=True, ondelete="CASCADE")

    # Extra days of gap required between upstream finishing and downstream
    # starting. 0 means downstream may start the next day.
    lag_days: int = Field(default=0, ge=0)

    origin: DependencyOrigin = DependencyOrigin.HUMAN

    created_at: datetime = Field(default_factory=_utcnow)


class Suggestion(SQLModel, table=True):
    """An LLM-proposed edge awaiting human review.

    Deliberately a separate table from Dependency. A suggestion is not part of
    the graph until a person accepts it and it passes the engine's cycle check,
    so the model has no write path into the graph at all.
    """

    __table_args__ = (
        UniqueConstraint("upstream_id", "downstream_id", name="uq_suggestion_edge"),
    )

    id: str = Field(default_factory=_new_id, primary_key=True)

    upstream_id: str = Field(foreign_key="task.id", index=True, ondelete="CASCADE")
    downstream_id: str = Field(foreign_key="task.id", index=True, ondelete="CASCADE")

    # The model's self-reported confidence, 0..1. Shown to the reviewer;
    # never used to auto-accept anything.
    confidence: float = Field(ge=0.0, le=1.0)

    # Must quote evidence from the task text, so the reviewer judges the
    # reasoning rather than the number.
    rationale: str

    status: SuggestionStatus = Field(default=SuggestionStatus.PENDING, index=True)

    created_at: datetime = Field(default_factory=_utcnow)
