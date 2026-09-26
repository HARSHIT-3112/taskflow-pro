"""Invariant tests for the dependency engine.

These tests encode the rules from the problem statement as invariants rather
than examples. Each one names the rule it protects. They need no database and
run in milliseconds, which is exactly why the engine was kept pure.

Rules under test:
  1. No cycles       - an edge that would close a loop is refused, with a path.
  2. No compounding  - converging paths move a task once, not once per path.
  3. Reversibility   - DONE -> IN_PROGRESS re-blocks dependents transitively.
  4. Persistence     - (covered by API tests; the engine is stateless)
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain import (
    CycleError,
    DependencyState,
    Edge,
    TaskNode,
    TaskStatus,
)
from app.engine.graph import (
    critical_path,
    find_cycle_path,
    recompute,
    topological_order,
)

START = date(2026, 1, 5)  # an arbitrary Monday


def task(
    task_id: str,
    *,
    start: date = START,
    days: int = 1,
    status: TaskStatus = TaskStatus.BACKLOG,
    pinned: bool = False,
) -> TaskNode:
    """Build a TaskNode with sensible defaults, so tests stay readable."""
    return TaskNode(
        id=task_id,
        duration_days=days,
        start_date=start,
        status=status,
        pinned=pinned,
    )


def diamond() -> tuple[list[TaskNode], list[Edge]]:
    """The graph from the problem statement.

        A ─┬─> B ─┬─> D
           └─> C ─┘

    Two distinct paths carry A's schedule to D. A naive delta-propagating
    engine moves D twice; this engine must move it once.
    """
    nodes = [
        task("A", start=START, days=3),
        task("B", start=START, days=2),
        task("C", start=START, days=2),
        task("D", start=START, days=2),
    ]
    edges = [
        Edge("A", "B"),
        Edge("A", "C"),
        Edge("B", "D"),
        Edge("C", "D"),
    ]
    return nodes, edges


def chain(length: int) -> tuple[list[TaskNode], list[Edge]]:
    """A straight chain T0 -> T1 -> ... so multi-level propagation is testable."""
    nodes = [task(f"T{i}", start=START, days=2) for i in range(length)]
    edges = [Edge(f"T{i}", f"T{i + 1}") for i in range(length - 1)]
    return nodes, edges


# ---------------------------------------------------------------------------
# Topological order
# ---------------------------------------------------------------------------


class TestTopologicalOrder:
    def test_every_prerequisite_comes_before_its_dependent(self) -> None:
        nodes, edges = diamond()
        order = topological_order([n.id for n in nodes], edges)

        position = {node_id: i for i, node_id in enumerate(order)}
        for edge in edges:
            assert position[edge.upstream_id] < position[edge.downstream_id], (
                f"{edge.upstream_id} must be ordered before {edge.downstream_id}"
            )

    def test_includes_disconnected_nodes(self) -> None:
        """A task with no dependencies still has to be scheduled."""
        nodes = [task("A"), task("B"), task("lonely")]
        order = topological_order([n.id for n in nodes], [Edge("A", "B")])
        assert set(order) == {"A", "B", "lonely"}

    def test_is_deterministic(self) -> None:
        """Same input, same order - otherwise tests and diffs are unstable."""
        nodes, edges = diamond()
        ids = [n.id for n in nodes]
        assert topological_order(ids, edges) == topological_order(ids, edges)

    def test_raises_on_a_cycle(self) -> None:
        edges = [Edge("A", "B"), Edge("B", "C"), Edge("C", "A")]
        with pytest.raises(CycleError) as excinfo:
            topological_order(["A", "B", "C"], edges)
        assert set(excinfo.value.path) >= {"A", "B", "C"}


# ---------------------------------------------------------------------------
# Rule 1: no cycles
# ---------------------------------------------------------------------------


class TestCycleDetection:
    def test_allows_an_edge_that_keeps_the_graph_acyclic(self) -> None:
        nodes, edges = diamond()
        assert find_cycle_path([n.id for n in nodes], edges, "B", "C") is None

    def test_detects_a_direct_two_task_loop(self) -> None:
        path = find_cycle_path(["A", "B"], [Edge("A", "B")], "B", "A")
        assert path is not None
        assert path[0] == path[-1], "a cycle path must start and end at the same task"

    def test_detects_the_three_task_loop_from_the_problem_statement(self) -> None:
        """A -> B -> C exists; adding C -> A must be refused."""
        edges = [Edge("A", "B"), Edge("B", "C")]
        path = find_cycle_path(["A", "B", "C"], edges, "C", "A")

        assert path is not None
        assert path[0] == path[-1]
        assert set(path) == {"A", "B", "C"}

    def test_detects_self_dependency(self) -> None:
        assert find_cycle_path(["A"], [], "A", "A") is not None

    def test_reports_the_offending_path_for_a_readable_error(self) -> None:
        """The user must be told WHICH chain is circular, not just that one is."""
        edges = [Edge("A", "B"), Edge("B", "C"), Edge("C", "D")]
        path = find_cycle_path(["A", "B", "C", "D"], edges, "D", "A")

        assert path == ["A", "B", "C", "D", "A"]

    def test_detection_does_not_mutate_the_graph(self) -> None:
        """Refusing an edge must leave the existing valid graph untouched."""
        nodes, edges = diamond()
        before = list(edges)

        find_cycle_path([n.id for n in nodes], edges, "D", "A")

        assert edges == before, "cycle detection must be read-only"


# ---------------------------------------------------------------------------
# Rule 2: no compounding  (the central correctness hazard)
# ---------------------------------------------------------------------------


class TestNoCompounding:
    def test_diamond_moves_downstream_once_not_once_per_path(self) -> None:
        """THE headline rule: A extended by 3 days moves D by 3 days, not 6."""
        nodes, edges = diamond()
        before = recompute(nodes, edges)

        # Extend A from 3 days to 6 days.
        extended = [
            task("A", start=START, days=6) if n.id == "A" else n for n in nodes
        ]
        after = recompute(extended, edges)

        shift = (after["D"].start_date - before["D"].start_date).days
        assert shift == 3, f"D moved {shift} days; expected 3, and 6 means compounding"

    def test_both_intermediate_paths_move_by_the_same_amount(self) -> None:
        """B and C each sit one hop from A, so both move by exactly the delay."""
        nodes, edges = diamond()
        before = recompute(nodes, edges)

        extended = [
            task("A", start=START, days=6) if n.id == "A" else n for n in nodes
        ]
        after = recompute(extended, edges)

        assert (after["B"].start_date - before["B"].start_date).days == 3
        assert (after["C"].start_date - before["C"].start_date).days == 3

    def test_extra_converging_paths_do_not_change_the_result(self) -> None:
        """Scheduling must not depend on HOW MANY paths connect two tasks.

        Adding a third parallel route A -> E -> D must leave D exactly where
        the two-path diamond put it.
        """
        nodes, edges = diamond()
        two_paths = recompute(nodes, edges)

        nodes_three = [*nodes, task("E", start=START, days=2)]
        edges_three = [*edges, Edge("A", "E"), Edge("E", "D")]
        three_paths = recompute(nodes_three, edges_three)

        assert three_paths["D"].start_date == two_paths["D"].start_date

    def test_downstream_task_starts_the_day_after_its_latest_prerequisite(self) -> None:
        """The actual scheduling arithmetic, stated plainly."""
        nodes, edges = diamond()
        result = recompute(nodes, edges)

        latest_prerequisite_end = max(result["B"].end_date, result["C"].end_date)
        assert result["D"].start_date == latest_prerequisite_end + timedelta(days=1)

    def test_propagates_through_every_level_of_a_deep_chain(self) -> None:
        """A change at the head must reach the tail, not stop partway."""
        nodes, edges = chain(6)
        before = recompute(nodes, edges)

        extended = [
            task("T0", start=START, days=7) if n.id == "T0" else n for n in nodes
        ]
        after = recompute(extended, edges)

        for i in range(1, 6):
            shift = (after[f"T{i}"].start_date - before[f"T{i}"].start_date).days
            assert shift == 5, f"T{i} moved {shift} days, expected 5"

    def test_recompute_is_idempotent(self) -> None:
        """Running it twice must change nothing.

        This is what makes one edit and ten edits behave identically, and it
        only holds because the engine computes absolute positions rather than
        applying relative shifts.
        """
        nodes, edges = diamond()
        first = recompute(nodes, edges)

        fed_back = [
            TaskNode(
                id=n.id,
                duration_days=n.duration_days,
                start_date=first[n.id].start_date,
                status=n.status,
                pinned=n.pinned,
            )
            for n in nodes
        ]
        second = recompute(fed_back, edges)

        for node_id, scheduled in first.items():
            assert second[node_id].start_date == scheduled.start_date
            assert second[node_id].end_date == scheduled.end_date


# ---------------------------------------------------------------------------
# Rule 3: blocked / ready and reversibility
# ---------------------------------------------------------------------------


class TestDependencyState:
    def test_task_without_prerequisites_is_ready(self) -> None:
        result = recompute([task("A")], [])
        assert result["A"].dependency_state is DependencyState.READY

    def test_task_is_blocked_while_a_prerequisite_is_unfinished(self) -> None:
        nodes = [task("A", status=TaskStatus.IN_PROGRESS), task("B")]
        result = recompute(nodes, [Edge("A", "B")])
        assert result["B"].dependency_state is DependencyState.BLOCKED

    def test_task_becomes_ready_once_every_prerequisite_is_done(self) -> None:
        nodes = [
            task("A", status=TaskStatus.DONE),
            task("B", status=TaskStatus.DONE),
            task("C"),
        ]
        result = recompute(nodes, [Edge("A", "C"), Edge("B", "C")])
        assert result["C"].dependency_state is DependencyState.READY

    def test_one_unfinished_prerequisite_is_enough_to_block(self) -> None:
        nodes = [
            task("A", status=TaskStatus.DONE),
            task("B", status=TaskStatus.REVIEW),
            task("C"),
        ]
        result = recompute(nodes, [Edge("A", "C"), Edge("B", "C")])
        assert result["C"].dependency_state is DependencyState.BLOCKED

    def test_blocked_and_ready_are_independent_of_the_column(self) -> None:
        """A BACKLOG task can be READY and an IN_PROGRESS task can be BLOCKED."""
        nodes = [
            task("done", status=TaskStatus.DONE),
            task("backlog", status=TaskStatus.BACKLOG),
            task("working", status=TaskStatus.IN_PROGRESS),
            task("todo", status=TaskStatus.BACKLOG),
        ]
        edges = [Edge("done", "backlog"), Edge("working", "todo")]
        result = recompute(nodes, edges)

        assert result["backlog"].dependency_state is DependencyState.READY
        assert result["todo"].dependency_state is DependencyState.BLOCKED


class TestRollbackOnRegression:
    def test_moving_a_done_task_back_reblocks_its_dependent(self) -> None:
        """The rule, stated directly."""
        finished = [task("A", status=TaskStatus.DONE), task("B")]
        assert recompute(finished, [Edge("A", "B")])["B"].dependency_state is (
            DependencyState.READY
        )

        regressed = [task("A", status=TaskStatus.IN_PROGRESS), task("B")]
        assert recompute(regressed, [Edge("A", "B")])["B"].dependency_state is (
            DependencyState.BLOCKED
        )

    def test_regression_reblocks_transitively_down_a_chain(self) -> None:
        """Re-blocking must reach every level, not just the direct dependent."""
        done_chain = [
            task("T0", status=TaskStatus.DONE),
            task("T1", status=TaskStatus.DONE),
            task("T2", status=TaskStatus.DONE),
            task("T3"),
        ]
        edges = [Edge("T0", "T1"), Edge("T1", "T2"), Edge("T2", "T3")]
        assert recompute(done_chain, edges)["T3"].dependency_state is (
            DependencyState.READY
        )

        # T1 regresses. T2 depends on it directly, T3 transitively.
        regressed = [
            task("T0", status=TaskStatus.DONE),
            task("T1", status=TaskStatus.IN_PROGRESS),
            task("T2", status=TaskStatus.IN_PROGRESS),
            task("T3"),
        ]
        result = recompute(regressed, edges)
        assert result["T2"].dependency_state is DependencyState.BLOCKED
        assert result["T3"].dependency_state is DependencyState.BLOCKED


# ---------------------------------------------------------------------------
# Scheduling policy: forward-only, pinning, provenance
# ---------------------------------------------------------------------------


class TestSchedulingPolicy:
    def test_an_early_finish_does_not_drag_downstream_work_forward(self) -> None:
        """Forward-only scheduling, a documented assumption.

        Shortening a prerequisite leaves the dependent where it is rather than
        silently pulling the team's plan earlier.

        Note the second recompute is fed B's PERSISTED date from the first pass,
        which is how the API calls it: recompute, save, recompute again later.
        """
        nodes = [task("A", start=START, days=5), task("B", start=START)]
        edges = [Edge("A", "B")]
        before = recompute(nodes, edges)

        shortened = [
            task("A", start=START, days=1),
            task("B", start=before["B"].start_date),
        ]
        after = recompute(shortened, edges)

        assert after["B"].start_date == before["B"].start_date

    def test_lag_days_add_a_gap_after_the_prerequisite(self) -> None:
        nodes = [task("A", start=START, days=2), task("B", start=START)]
        no_lag = recompute(nodes, [Edge("A", "B", lag_days=0)])
        with_lag = recompute(nodes, [Edge("A", "B", lag_days=3)])

        delta = (with_lag["B"].start_date - no_lag["B"].start_date).days
        assert delta == 3

    def test_a_pinned_task_is_never_moved(self) -> None:
        pinned_start = START + timedelta(days=20)
        nodes = [
            task("A", start=START, days=3),
            task("B", start=pinned_start, pinned=True),
        ]
        result = recompute(nodes, [Edge("A", "B")])
        assert result["B"].start_date == pinned_start

    def test_a_pin_that_conflicts_with_prerequisites_is_flagged(self) -> None:
        """The engine honours the pin but reports the impossible schedule."""
        nodes = [
            task("A", start=START, days=30),
            task("B", start=START + timedelta(days=1), pinned=True),
        ]
        result = recompute(nodes, [Edge("A", "B")])

        assert result["B"].start_date == START + timedelta(days=1)
        assert result["B"].over_constrained is True

    def test_end_date_is_derived_from_duration(self) -> None:
        """A one-day task starts and ends on the same day."""
        result = recompute([task("A", start=START, days=1)], [])
        assert result["A"].end_date == START

        result = recompute([task("A", start=START, days=4)], [])
        assert result["A"].end_date == START + timedelta(days=3)

    def test_records_which_prerequisite_set_the_date(self) -> None:
        """Binding-constraint provenance: the UI can say WHY a date is what it is."""
        nodes = [
            task("short", start=START, days=1),
            task("long", start=START, days=10),
            task("D", start=START),
        ]
        result = recompute(nodes, [Edge("short", "D"), Edge("long", "D")])

        assert result["D"].binding_constraint_id == "long"


# ---------------------------------------------------------------------------
# Critical path
# ---------------------------------------------------------------------------


class TestCriticalPath:
    def test_finds_the_longest_chain_by_duration(self) -> None:
        nodes = [
            task("A", days=1),
            task("slow", days=10),
            task("fast", days=1),
            task("D", days=1),
        ]
        edges = [Edge("A", "slow"), Edge("A", "fast"), Edge("slow", "D"), Edge("fast", "D")]

        assert critical_path(nodes, edges) == ["A", "slow", "D"]

    def test_handles_an_empty_board(self) -> None:
        assert critical_path([], []) == []

    def test_single_task_is_its_own_critical_path(self) -> None:
        assert critical_path([task("A", days=3)], []) == ["A"]


# ---------------------------------------------------------------------------
# Edge cases named in the synopsis
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_graph(self) -> None:
        assert recompute([], []) == {}

    def test_disconnected_subgraphs_are_scheduled_independently(self) -> None:
        nodes = [task("A", days=5), task("B"), task("X", days=5), task("Y")]
        result = recompute(nodes, [Edge("A", "B"), Edge("X", "Y")])

        assert result["B"].start_date == result["Y"].start_date

    def test_edges_referring_to_missing_tasks_are_ignored(self) -> None:
        """A deleted task must not break scheduling for everyone else."""
        result = recompute([task("A")], [Edge("ghost", "A")])
        assert result["A"].dependency_state is DependencyState.READY

    def test_a_task_with_many_prerequisites_waits_for_the_latest(self) -> None:
        nodes = [
            task("p1", start=START, days=2),
            task("p2", start=START, days=9),
            task("p3", start=START, days=4),
            task("D", start=START),
        ]
        edges = [Edge("p1", "D"), Edge("p2", "D"), Edge("p3", "D")]
        result = recompute(nodes, edges)

        assert result["D"].start_date == result["p2"].end_date + timedelta(days=1)
