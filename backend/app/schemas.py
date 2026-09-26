"""Request and response shapes for the HTTP API.

These are deliberately separate from the database models. A client must not be
able to set `end_date`, `dependency_state` or `version` directly - those are
the engine's to write - so the create/update schemas simply do not contain them.
Validation happens here, before any handler code runs.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.domain import DependencyOrigin, DependencyState, TaskStatus

# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


class TaskCreate(BaseModel):
    """Fields a client may supply when creating a task.

    Note what is absent: end_date (derived from duration), dependency_state
    (derived from the graph) and version (managed by the server).
    """

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    status: TaskStatus = TaskStatus.BACKLOG
    start_date: date
    duration_days: int = Field(default=1, ge=1, le=365)
    pinned: bool = False


class TaskUpdate(BaseModel):
    """Partial update. Every field optional; only what is sent is changed.

    `version` is required: it is the caller asserting which revision of the task
    they were looking at when they made the edit. A mismatch means someone else
    changed it first, and the write is refused instead of silently clobbering.
    """

    version: int

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    status: TaskStatus | None = None
    start_date: date | None = None
    duration_days: int | None = Field(default=None, ge=1, le=365)
    pinned: bool | None = None


class TaskMove(BaseModel):
    """Drag-and-drop: which column, and where within it."""

    version: int
    status: TaskStatus
    position: float


class TaskRead(BaseModel):
    """A task as the client sees it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str
    status: TaskStatus
    start_date: date
    end_date: date
    duration_days: int
    pinned: bool
    position: float
    dependency_state: DependencyState
    version: int
    binding_constraint_id: str | None


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


class DependencyCreate(BaseModel):
    upstream_id: str
    downstream_id: str
    lag_days: int = Field(default=0, ge=0, le=365)


class DependencyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    upstream_id: str
    downstream_id: str
    lag_days: int
    origin: DependencyOrigin


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class BoardRead(BaseModel):
    """The whole board: every task plus every edge."""

    tasks: list[TaskRead]
    dependencies: list[DependencyRead]
    critical_path: list[str]


class MutationResult(BaseModel):
    """What every mutating endpoint returns.

    `affected` carries every task whose schedule or dependency state changed as
    a result of this one edit - not just the task the client touched. That lets
    the client reconcile the whole cascade in a single round trip instead of
    refetching the board.
    """

    affected: list[TaskRead]
    dependencies: list[DependencyRead]
    critical_path: list[str]

    # Tasks whose pin conflicts with their prerequisites. Surfaced rather than
    # silently resolved, so the user can see the schedule is impossible.
    over_constrained_ids: list[str] = []


class CycleRejection(BaseModel):
    """The 409 body when a dependency would close a loop.

    `path` is the actual offending chain so the UI can name it, e.g.
    "Design schema -> Build API -> Write tests -> Design schema".
    """

    detail: str
    path: list[str]
    titles: list[str]
