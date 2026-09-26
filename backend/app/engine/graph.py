"""The dependency engine.

This module is pure: it imports only the standard library and app.domain. It
knows nothing about FastAPI, SQLModel or Postgres. Everything here is a
function that takes plain data and returns plain data, which means the whole
engine can be tested in milliseconds without a database.

The four rules the engine enforces:

1. No cycles      - `find_cycle_path` refuses an edge before it is persisted.
2. No compounding - `recompute` recalculates ABSOLUTE dates in topological
                    order rather than propagating deltas along edges.
3. Reversibility  - `recompute` derives BLOCKED/READY from current prerequisite
                    status every time, so nothing needs "un-flipping".
4. Determinism    - the same input always produces the same output, and running
                    recompute twice changes nothing (idempotence).
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, timedelta

from app.domain import (
    CycleError,
    DependencyState,
    Edge,
    ScheduledTask,
    TaskNode,
    TaskStatus,
)

# An adjacency list: node id -> ids of the nodes it points at.
AdjacencyList = dict[str, list[str]]


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------


def live_edges(node_ids: list[str] | set[str], edges: list[Edge]) -> list[Edge]:
    """Keep only edges whose BOTH endpoints still exist.

    A dangling edge - one pointing at a task that has been deleted - would
    otherwise inflate a task's prerequisite count with a prerequisite that can
    never complete, and Kahn's algorithm would report a phantom cycle. Deleting
    a task must never break scheduling for everyone else, so every entry point
    filters through here first.
    """
    present = set(node_ids)
    return [
        edge
        for edge in edges
        if edge.upstream_id in present and edge.downstream_id in present
    ]


def build_forward_adjacency(node_ids: list[str], edges: list[Edge]) -> AdjacencyList:
    """upstream id -> [downstream ids]  ("what waits on this task?")"""
    adjacency: AdjacencyList = {node_id: [] for node_id in node_ids}
    for edge in live_edges(adjacency.keys(), edges):
        adjacency[edge.upstream_id].append(edge.downstream_id)
    return adjacency


def build_reverse_adjacency(node_ids: list[str], edges: list[Edge]) -> AdjacencyList:
    """downstream id -> [upstream ids]  ("what does this task wait on?")"""
    adjacency: AdjacencyList = {node_id: [] for node_id in node_ids}
    for edge in live_edges(adjacency.keys(), edges):
        adjacency[edge.downstream_id].append(edge.upstream_id)
    return adjacency


# ---------------------------------------------------------------------------
# Rule 1: cycle detection
# ---------------------------------------------------------------------------


def find_path(adjacency: AdjacencyList, start: str, goal: str) -> list[str] | None:
    """Return a path from `start` to `goal` following edge direction, or None.

    Breadth-first search, so the path returned is the shortest one - which
    makes for a readable error message. We record how we reached each node
    (`came_from`) so the actual path can be reconstructed rather than just
    answering yes/no.
    """
    if start == goal:
        return [start]

    came_from: dict[str, str] = {}
    seen = {start}
    queue = deque([start])

    while queue:
        current = queue.popleft()
        for neighbour in adjacency.get(current, []):
            if neighbour in seen:
                continue
            seen.add(neighbour)
            came_from[neighbour] = current

            if neighbour == goal:
                # Walk backwards from the goal to rebuild the path, then flip it.
                path = [goal]
                while path[-1] != start:
                    path.append(came_from[path[-1]])
                path.reverse()
                return path

            queue.append(neighbour)

    return None


def find_cycle_path(
    node_ids: list[str],
    edges: list[Edge],
    new_upstream_id: str,
    new_downstream_id: str,
) -> list[str] | None:
    """Would adding upstream -> downstream create a cycle? If so, return it.

    The insight: adding U -> V closes a loop exactly when V can already reach U
    by following existing edges. So we search forwards from V and see whether we
    arrive at U.

    Returns the offending path (e.g. ["a", "b", "c", "a"]) so the user can be
    told which chain is circular, or None when the edge is safe.

    This runs BEFORE the edge is written, inside the same transaction that would
    write it, so a refusal leaves the stored graph untouched.
    """
    # A task depending on itself is the degenerate one-node cycle.
    if new_upstream_id == new_downstream_id:
        return [new_upstream_id, new_downstream_id]

    forward = build_forward_adjacency(node_ids, edges)
    path_back = find_path(forward, new_downstream_id, new_upstream_id)

    if path_back is None:
        return None

    # path_back runs V -> ... -> U. Appending V closes the loop for display:
    # V -> ... -> U -> V.
    return [*path_back, new_downstream_id]


# ---------------------------------------------------------------------------
# Topological order (Kahn's algorithm)
# ---------------------------------------------------------------------------


def topological_order(node_ids: list[str], edges: list[Edge]) -> list[str]:
    """Order nodes so every task appears after all of its prerequisites.

    Kahn's algorithm:
      1. Count how many prerequisites each task has (its "in-degree").
      2. Start with every task that has none - nothing blocks them.
      3. Take one, output it, and tell each of its dependents "one fewer
         prerequisite to wait for". Any dependent that drops to zero is now
         ready, so it joins the queue.
      4. Repeat.

    If we finish having output fewer nodes than we started with, the leftovers
    form a cycle - none of them ever reached in-degree zero - so we raise.

    This ordering is the foundation of the whole engine: processing tasks in it
    guarantees that when we compute a task's start date, every prerequisite has
    already been given its final dates.

    Sorting the ready queue keeps the output deterministic, so identical input
    always yields an identical order and tests are stable.
    """
    edges = live_edges(node_ids, edges)

    in_degree: dict[str, int] = {node_id: 0 for node_id in node_ids}
    forward = build_forward_adjacency(node_ids, edges)

    for edge in edges:
        in_degree[edge.downstream_id] += 1

    ready = deque(sorted([n for n, degree in in_degree.items() if degree == 0]))
    ordered: list[str] = []

    while ready:
        current = ready.popleft()
        ordered.append(current)

        newly_ready = []
        for dependent in forward[current]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                newly_ready.append(dependent)

        # Sort each batch so the traversal is reproducible run to run.
        for node_id in sorted(newly_ready):
            ready.append(node_id)

    if len(ordered) != len(node_ids):
        stuck = sorted(set(node_ids) - set(ordered))
        raise CycleError(_describe_cycle(stuck, edges))

    return ordered


def _describe_cycle(stuck_ids: list[str], edges: list[Edge]) -> list[str]:
    """Best-effort readable cycle from the nodes that never became ready."""
    stuck = set(stuck_ids)
    adjacency = build_forward_adjacency(sorted(stuck), [
        e for e in edges if e.upstream_id in stuck and e.downstream_id in stuck
    ])

    for start in sorted(stuck):
        for neighbour in adjacency.get(start, []):
            back = find_path(adjacency, neighbour, start)
            if back is not None:
                return [start, *back]

    return sorted(stuck)


# ---------------------------------------------------------------------------
# Rules 2 and 3: recompute
# ---------------------------------------------------------------------------


def recompute(nodes: list[TaskNode], edges: list[Edge]) -> dict[str, ScheduledTask]:
    """Recalculate dates and blocked/ready state for the whole graph.

    THIS IS THE NO-COMPOUNDING FIX, and it is worth being precise about why.

    The naive approach propagates deltas along edges: "A moved +3, so push
    everything downstream by +3". In a diamond (A feeds B and C; B and C both
    feed D) the +3 arrives at D twice, once down each path, and D moves +6.
    Adding a visited-set patch on top only papers over the wrong model.

    Instead this function never looks at deltas at all. It walks the graph in
    topological order and recalculates each task's ABSOLUTE start date from its
    prerequisites' final end dates:

        start = max(end of each prerequisite + 1 + that edge's lag)

    Every node is visited exactly once, no matter how many paths reach it, so
    the diamond gives D exactly +3. Double counting is not prevented here - it
    is impossible to express.

    Two further properties fall out:

    * Idempotence - running this twice changes nothing, because it computes an
      absolute position rather than applying a relative shift.
    * Forward-only scheduling - a task never moves EARLIER than where it
      already sits (see `max(node.start_date, ...)` below). An upstream slip
      pushes work later; an upstream finishing early leaves the slack visible
      instead of silently dragging the team's plan forward.
    """
    node_by_id = {node.id: node for node in nodes}
    node_ids = list(node_by_id)

    # Raises CycleError if the graph is not a DAG. Callers validate edges before
    # persisting them, so reaching this with a cycle means stored data is bad.
    order = topological_order(node_ids, edges)

    # Group incoming edges by the task that waits, so each task can ask
    # "what am I waiting for?" in one lookup.
    incoming: dict[str, list[Edge]] = defaultdict(list)
    for edge in edges:
        if edge.downstream_id in node_by_id and edge.upstream_id in node_by_id:
            incoming[edge.downstream_id].append(edge)

    results: dict[str, ScheduledTask] = {}

    for node_id in order:
        node = node_by_id[node_id]
        prerequisites = incoming.get(node_id, [])

        earliest_start: date | None = None
        binding_constraint_id: str | None = None

        for edge in prerequisites:
            # Safe because topological order guarantees every prerequisite has
            # already been scheduled by the time we reach this task.
            upstream_result = results[edge.upstream_id]

            # +1 because a task starts the day AFTER its prerequisite ends.
            candidate = upstream_result.end_date + timedelta(days=1 + edge.lag_days)

            if earliest_start is None or candidate > earliest_start:
                earliest_start = candidate
                # Record WHICH prerequisite set the date, not just the date.
                binding_constraint_id = edge.upstream_id

        if node.pinned:
            # A pinned task never moves. If its prerequisites demanded a later
            # start, honour the pin but flag the impossible schedule.
            start_date = node.start_date
            over_constrained = (
                earliest_start is not None and earliest_start > node.start_date
            )
            if not over_constrained:
                # Nothing is actually holding this task back; it sits where
                # the user pinned it.
                binding_constraint_id = None
        elif earliest_start is None:
            # No prerequisites: the task keeps the date the user gave it.
            start_date = node.start_date
            over_constrained = False
        else:
            # Forward-only: take the later of where it already is and where its
            # prerequisites allow. This is what stops an early finish from
            # silently pulling downstream work forward.
            start_date = max(node.start_date, earliest_start)
            over_constrained = False
            if start_date != earliest_start:
                # The task's own date already sat later than anything required,
                # so no prerequisite is actually binding it.
                binding_constraint_id = None

        end_date = start_date + timedelta(days=node.duration_days - 1)

        results[node_id] = ScheduledTask(
            id=node_id,
            start_date=start_date,
            end_date=end_date,
            dependency_state=_derive_state(node, prerequisites, node_by_id),
            binding_constraint_id=binding_constraint_id,
            over_constrained=over_constrained,
        )

    return results


def _derive_state(
    node: TaskNode,
    prerequisites: list[Edge],
    node_by_id: dict[str, TaskNode],
) -> DependencyState:
    """READY when every prerequisite is DONE, otherwise BLOCKED.

    This is recomputed from live prerequisite status on every pass and is never
    read back as an input. That is precisely why dragging a task from DONE back
    to IN_PROGRESS re-blocks its dependents with no rollback code anywhere: the
    state was never latched, so there is nothing to un-latch.
    """
    for edge in prerequisites:
        upstream = node_by_id.get(edge.upstream_id)
        if upstream is None:
            continue
        if upstream.status is not TaskStatus.DONE:
            return DependencyState.BLOCKED
    return DependencyState.READY


# ---------------------------------------------------------------------------
# Critical path
# ---------------------------------------------------------------------------


def critical_path(nodes: list[TaskNode], edges: list[Edge]) -> list[str]:
    """The longest dependency chain by duration: the tasks with zero slack.

    Reuses the same topological order. For each task we track the longest total
    duration of any chain ending at it; because prerequisites are always
    processed first, each task can simply extend the best chain among them.

    Any delay to a task on this path delays the whole project, which is what
    makes it worth highlighting.
    """
    node_by_id = {node.id: node for node in nodes}
    if not node_by_id:
        return []

    order = topological_order(list(node_by_id), edges)

    incoming: dict[str, list[Edge]] = defaultdict(list)
    for edge in edges:
        if edge.downstream_id in node_by_id and edge.upstream_id in node_by_id:
            incoming[edge.downstream_id].append(edge)

    # longest[x] = total duration of the longest chain ending at x
    longest: dict[str, int] = {}
    previous: dict[str, str | None] = {}

    for node_id in order:
        node = node_by_id[node_id]
        best_length = 0
        best_previous: str | None = None

        for edge in incoming.get(node_id, []):
            candidate = longest[edge.upstream_id] + edge.lag_days
            if candidate > best_length:
                best_length = candidate
                best_previous = edge.upstream_id

        longest[node_id] = best_length + node.duration_days
        previous[node_id] = best_previous

    # Walk back from whichever task ends the longest chain.
    end_id = max(longest, key=lambda n: (longest[n], n))
    path = [end_id]
    while previous[path[-1]] is not None:
        path.append(previous[path[-1]])  # type: ignore[arg-type]
    path.reverse()
    return path
