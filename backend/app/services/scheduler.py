"""The bridge between the database and the pure engine.

The engine knows nothing about SQL, so something has to:

    1. load rows from Postgres
    2. convert them into the engine's plain value types
    3. run the engine
    4. write the results back

That is this module, and it is the ONLY place allowed to write `start_date`,
`end_date`, `dependency_state` and `binding_constraint_id`.

Everything here runs inside the caller's transaction. The caller commits once,
at the end, so a rejected edit or a failed write leaves the stored graph exactly
as it was.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.domain import Edge, ScheduledTask, TaskNode
from app.engine.graph import critical_path, find_cycle_path, recompute
from app.models import Dependency, Task


def load_graph(session: Session, *, lock: bool = False) -> tuple[list[Task], list[Dependency]]:
    """Read every task and edge.

    `lock` issues SELECT ... FOR UPDATE, which holds a row lock until the
    transaction commits. Mutations take it so two concurrent edits are
    serialised by Postgres rather than racing; plain reads do not, so the board
    stays fast for viewers.

    The board is small (tens to hundreds of tasks), so loading it whole keeps
    the code simple and the recompute exact. See docs/DESIGN.md for what would
    change at a scale where that stops being true.
    """
    task_query = select(Task)
    if lock:
        task_query = task_query.with_for_update()

    tasks = list(session.exec(task_query).all())
    dependencies = list(session.exec(select(Dependency)).all())
    return tasks, dependencies


def to_engine_types(
    tasks: list[Task], dependencies: list[Dependency]
) -> tuple[list[TaskNode], list[Edge]]:
    """Convert database rows into the engine's immutable value types.

    This conversion is what keeps the engine pure. It never sees a SQLModel
    object, a Session, or a request.
    """
    nodes = [
        TaskNode(
            id=task.id,
            duration_days=task.duration_days,
            start_date=task.start_date,
            status=task.status,
            pinned=task.pinned,
        )
        for task in tasks
    ]
    edges = [
        Edge(
            upstream_id=dependency.upstream_id,
            downstream_id=dependency.downstream_id,
            lag_days=dependency.lag_days,
        )
        for dependency in dependencies
    ]
    return nodes, edges


def apply_schedule(
    session: Session,
    tasks: list[Task],
    results: dict[str, ScheduledTask],
) -> list[Task]:
    """Write the engine's verdict back, returning only the rows that changed.

    Comparing before writing matters for two reasons: we avoid pointless UPDATE
    statements, and `version` only increments when something genuinely changed,
    so an unrelated open tab is not invalidated by a no-op recompute.
    """
    changed: list[Task] = []

    for task in tasks:
        result = results.get(task.id)
        if result is None:
            continue

        is_same = (
            task.start_date == result.start_date
            and task.end_date == result.end_date
            and task.dependency_state == result.dependency_state
            and task.binding_constraint_id == result.binding_constraint_id
        )
        if is_same:
            continue

        task.start_date = result.start_date
        task.end_date = result.end_date
        task.dependency_state = result.dependency_state
        task.binding_constraint_id = result.binding_constraint_id
        task.version += 1

        session.add(task)
        changed.append(task)

    return changed


def recompute_board(session: Session) -> tuple[list[Task], list[str], list[str]]:
    """Recompute the whole board and persist the result.

    Returns (changed tasks, critical path ids, over-constrained task ids).

    Called after every mutation. Because the engine recomputes absolute dates
    rather than applying deltas, calling this more often than strictly necessary
    is harmless - it is idempotent.
    """
    tasks, dependencies = load_graph(session, lock=True)
    nodes, edges = to_engine_types(tasks, dependencies)

    results = recompute(nodes, edges)
    changed = apply_schedule(session, tasks, results)

    over_constrained = sorted(r.id for r in results.values() if r.over_constrained)
    return changed, critical_path(nodes, edges), over_constrained


def check_cycle(
    session: Session, upstream_id: str, downstream_id: str
) -> list[str] | None:
    """Would this edge close a loop? Returns the offending path, or None.

    Runs inside the same transaction that would write the edge, and holds row
    locks on the tasks, so a concurrent insert cannot slip a second edge in
    between the check and the write and create a cycle neither call saw alone.
    """
    tasks, dependencies = load_graph(session, lock=True)
    nodes, edges = to_engine_types(tasks, dependencies)

    return find_cycle_path([n.id for n in nodes], edges, upstream_id, downstream_id)


def preview_recompute(
    session: Session,
    *,
    task_id: str,
    duration_days: int | None = None,
    start_date: object | None = None,
) -> dict[str, ScheduledTask]:
    """Score a hypothetical edit WITHOUT writing anything (the dry-run preview).

    This costs almost nothing to provide: the engine has no side effects, so we
    simply build a modified copy of the node list and run the same function. The
    session is never written to, so the caller can roll back or simply not commit.
    """
    tasks, dependencies = load_graph(session)
    nodes, edges = to_engine_types(tasks, dependencies)

    hypothetical = []
    for node in nodes:
        if node.id == task_id:
            node = TaskNode(
                id=node.id,
                duration_days=duration_days if duration_days is not None else node.duration_days,
                start_date=start_date or node.start_date,  # type: ignore[arg-type]
                status=node.status,
                pinned=node.pinned,
            )
        hypothetical.append(node)

    return recompute(hypothetical, edges)
