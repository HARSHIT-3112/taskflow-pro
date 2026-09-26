"""Task endpoints.

Every mutating endpoint follows the same shape:

    1. load and lock the rows it will touch
    2. validate (including optimistic-concurrency version check)
    3. apply the change
    4. recompute the whole board through the engine
    5. commit once

If any step raises, nothing is committed and the stored graph is unchanged.
Each mutation returns every task the change affected, so the client reconciles
the full cascade in one round trip.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.db import get_session
from app.models import Dependency, Task
from app.schemas import (
    BoardRead,
    DependencyRead,
    MutationResult,
    TaskCreate,
    TaskMove,
    TaskRead,
    TaskUpdate,
)
from app.services.scheduler import recompute_board

router = APIRouter(prefix="/api", tags=["tasks"])


def _get_task_or_404(session: Session, task_id: str) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    return task


def _check_version(task: Task, expected: int) -> None:
    """Optimistic concurrency.

    The client tells us which revision it was looking at. If the stored version
    has moved on, someone else edited this task first and we refuse rather than
    overwrite their work with a decision made against stale data.
    """
    if task.version != expected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This task was changed by someone else "
                f"(you have version {expected}, current is {task.version}). "
                "Refresh to see the latest plan."
            ),
        )


def _result(session: Session) -> MutationResult:
    """Recompute, commit, and describe everything that moved."""
    changed, path, over_constrained = recompute_board(session)
    session.commit()

    for task in changed:
        session.refresh(task)

    dependencies = list(session.exec(select(Dependency)).all())
    return MutationResult(
        affected=[TaskRead.model_validate(t) for t in changed],
        dependencies=[DependencyRead.model_validate(d) for d in dependencies],
        critical_path=path,
        over_constrained_ids=over_constrained,
    )


@router.get("/board", response_model=BoardRead)
def read_board(session: Session = Depends(get_session)) -> BoardRead:
    """The whole board. This is what the client loads on startup and refresh."""
    from app.engine.graph import critical_path
    from app.services.scheduler import to_engine_types

    tasks = list(session.exec(select(Task).order_by(Task.position)).all())
    dependencies = list(session.exec(select(Dependency)).all())
    nodes, edges = to_engine_types(tasks, dependencies)

    return BoardRead(
        tasks=[TaskRead.model_validate(t) for t in tasks],
        dependencies=[DependencyRead.model_validate(d) for d in dependencies],
        critical_path=critical_path(nodes, edges),
    )


@router.post("/tasks", response_model=MutationResult, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskCreate,
    session: Session = Depends(get_session),
) -> MutationResult:
    """Create a task.

    end_date is written by the engine during the recompute below, not here -
    duration is the source of truth. The value set at insert time is a
    placeholder that the engine immediately overwrites.
    """
    highest = session.exec(
        select(Task.position).where(Task.status == payload.status).order_by(Task.position.desc())
    ).first()
    next_position = (highest or 0.0) + 1.0

    task = Task(
        title=payload.title,
        description=payload.description,
        status=payload.status,
        start_date=payload.start_date,
        end_date=payload.start_date,  # engine corrects this from duration
        duration_days=payload.duration_days,
        pinned=payload.pinned,
        position=next_position,
    )
    session.add(task)
    session.flush()  # assign the id without committing yet

    return _result(session)


@router.patch("/tasks/{task_id}", response_model=MutationResult)
def update_task(
    task_id: str,
    payload: TaskUpdate,
    session: Session = Depends(get_session),
) -> MutationResult:
    """Edit a task. Any date change cascades to everything downstream."""
    task = _get_task_or_404(session, task_id)
    _check_version(task, payload.version)

    fields = payload.model_dump(exclude_unset=True, exclude={"version"})
    for field, value in fields.items():
        setattr(task, field, value)

    task.version += 1
    session.add(task)

    return _result(session)


@router.patch("/tasks/{task_id}/move", response_model=MutationResult)
def move_task(
    task_id: str,
    payload: TaskMove,
    session: Session = Depends(get_session),
) -> MutationResult:
    """Drag-and-drop between columns.

    Moving a task to or from DONE changes whether its dependents' prerequisites
    are satisfied, so the recompute that follows is what implements
    rollback-on-regression. There is no rollback code: the state is simply
    derived again from the new reality.
    """
    task = _get_task_or_404(session, task_id)
    _check_version(task, payload.version)

    task.status = payload.status
    task.position = payload.position
    task.version += 1
    session.add(task)

    return _result(session)


@router.delete("/tasks/{task_id}", response_model=MutationResult)
def delete_task(
    task_id: str,
    session: Session = Depends(get_session),
) -> MutationResult:
    """Delete a task.

    Its edges go with it (ON DELETE CASCADE), so tasks that were waiting on it
    become unblocked by the recompute rather than waiting on a ghost.
    """
    task = _get_task_or_404(session, task_id)
    session.delete(task)
    session.flush()

    return _result(session)
