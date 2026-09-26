"""The vocabulary of the problem: enums and plain value types.

This module imports nothing but the standard library. Both the database layer
(app/models.py) and the engine (app/engine/) depend on it, which keeps a single
definition of each concept while leaving the engine free of any database or
framework import.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date, timedelta


class TaskStatus(str, enum.Enum):
    """The four Kanban columns.

    Workflow stage only. This says nothing about whether a task is blocked -
    that is DependencyState, computed from the graph. A BACKLOG task can be
    READY, and an IN_PROGRESS task can be BLOCKED.
    """

    BACKLOG = "BACKLOG"
    IN_PROGRESS = "IN_PROGRESS"
    REVIEW = "REVIEW"
    DONE = "DONE"


class DependencyState(str, enum.Enum):
    """Derived from the graph on every recompute. Never set by hand."""

    READY = "READY"
    BLOCKED = "BLOCKED"


class DependencyOrigin(str, enum.Enum):
    HUMAN = "HUMAN"
    AI_ACCEPTED = "AI_ACCEPTED"


class SuggestionStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class TaskNode:
    """A task as the engine sees it.

    Frozen (immutable) on purpose: the engine never mutates its input. It reads
    nodes and returns a fresh set of results, which is what makes recompute
    safe to run speculatively for the dry-run preview.
    """

    id: str
    duration_days: int
    start_date: date
    status: TaskStatus
    pinned: bool = False

    @property
    def end_date(self) -> date:
        """Last working day of the task.

        duration_days is the source of truth and a 1-day task starts and ends
        on the same day, hence the -1.
        """
        return self.start_date + timedelta(days=self.duration_days - 1)


@dataclass(frozen=True)
class Edge:
    """A directed dependency: upstream must finish before downstream starts."""

    upstream_id: str
    downstream_id: str
    lag_days: int = 0


@dataclass(frozen=True)
class ScheduledTask:
    """The engine's verdict for one task after a recompute."""

    id: str
    start_date: date
    end_date: date
    dependency_state: DependencyState

    # Which prerequisite actually decided this task's start date. None when the
    # task has no prerequisites, or when its own date already sat later than
    # anything its prerequisites required.
    binding_constraint_id: str | None = None

    # True when the task is pinned but its prerequisites would have pushed it
    # later. The engine honours the pin and reports the conflict rather than
    # silently producing an impossible schedule.
    over_constrained: bool = False


class CycleError(Exception):
    """Raised when the graph contains a cycle.

    Carries the offending path so the API can tell the user exactly which
    chain is circular, e.g. ["a", "b", "c", "a"].
    """

    def __init__(self, path: list[str]) -> None:
        self.path = path
        readable = " -> ".join(path)
        super().__init__(f"Circular dependency: {readable}")
