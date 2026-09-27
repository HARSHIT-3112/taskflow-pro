"""Dependency (edge) endpoints.

This is where Rule 1 lives: an edge that would create a cycle is refused before
it is written, inside the same transaction that would have written it, so the
existing valid graph is left byte-for-byte unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.db import get_session
from app.domain import DependencyOrigin
from app.models import Dependency, Task
from app.schemas import DependencyCreate, DependencyRead, MutationResult, TaskRead
from app.services.scheduler import check_cycle, recompute_board

router = APIRouter(prefix="/api", tags=["dependencies"])


def _result(session: Session) -> MutationResult:
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


def create_edge(
    session: Session,
    upstream_id: str,
    downstream_id: str,
    lag_days: int,
    origin: DependencyOrigin,
) -> None:
    """Validate and insert one edge. Shared by manual creation and AI acceptance.

    Routing both paths through this function is deliberate: an accepted AI
    suggestion goes through exactly the same validation as a hand-drawn edge,
    so the model cannot bypass the cycle check even if a reviewer clicks
    accept carelessly.

    Raises HTTPException on any rule violation; the caller's transaction is
    then rolled back without having written anything.
    """
    if upstream_id == downstream_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A task cannot depend on itself.",
        )

    upstream = session.get(Task, upstream_id)
    downstream = session.get(Task, downstream_id)
    if upstream is None or downstream is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Both tasks must exist before they can be linked.",
        )

    existing = session.exec(
        select(Dependency).where(
            Dependency.upstream_id == upstream_id,
            Dependency.downstream_id == downstream_id,
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That dependency already exists.",
        )

    # THE CYCLE CHECK. Runs before the insert, inside this transaction, holding
    # row locks - so nothing is written and no concurrent insert can slip past.
    cycle = check_cycle(session, upstream_id, downstream_id)
    if cycle is not None:
        titles = []
        for task_id in cycle:
            task = session.get(Task, task_id)
            titles.append(task.title if task else task_id)

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "detail": (
                    "This dependency would create a circular relationship: "
                    + " -> ".join(titles)
                ),
                "path": cycle,
                "titles": titles,
            },
        )

    session.add(
        Dependency(
            upstream_id=upstream_id,
            downstream_id=downstream_id,
            lag_days=lag_days,
            origin=origin,
        )
    )
    session.flush()


@router.post(
    "/dependencies",
    response_model=MutationResult,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Would create a cycle, or already exists"}},
)
def add_dependency(
    payload: DependencyCreate,
    session: Session = Depends(get_session),
) -> MutationResult:
    """Add a prerequisite relationship.

    On success the schedule cascade is returned. On a cycle, 409 with the
    offending path - and the graph is untouched.
    """
    create_edge(
        session,
        payload.upstream_id,
        payload.downstream_id,
        payload.lag_days,
        DependencyOrigin.HUMAN,
    )
    return _result(session)


@router.delete("/dependencies/{dependency_id}", response_model=MutationResult)
def remove_dependency(
    dependency_id: str,
    session: Session = Depends(get_session),
) -> MutationResult:
    """Remove a prerequisite.

    The downstream task may become READY as a result, which the recompute
    works out on its own.
    """
    dependency = session.get(Dependency, dependency_id)
    if dependency is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dependency {dependency_id} not found",
        )

    session.delete(dependency)
    session.flush()

    return _result(session)
