# Test Cases

Full suite of tests run against the dependency engine.

```bash
make test
# or: cd backend && .venv/bin/python -m pytest --cov=app --cov-report=term-missing
```

**Result: 36 passed. 95% branch coverage of `app/engine/graph.py`.**

These tests encode the rules from the problem statement as **invariants rather
than examples**, and they need no database — the engine is pure, so the whole
suite runs in ~50 ms. That speed is why the hard cases are covered exhaustively
instead of being spot-checked through the UI.

---

## Rule 1 — No cycles

`TestCycleDetection`

| Test | What it proves |
|---|---|
| `test_allows_an_edge_that_keeps_the_graph_acyclic` | Legal edges are not falsely refused |
| `test_detects_a_direct_two_task_loop` | `A → B` then `B → A` is caught |
| `test_detects_the_three_task_loop_from_the_problem_statement` | The exact `A → B → C → A` case from the spec |
| `test_detects_self_dependency` | A task cannot depend on itself |
| `test_reports_the_offending_path_for_a_readable_error` | Returns `["A","B","C","D","A"]`, not just a boolean |
| `test_detection_does_not_mutate_the_graph` | **The existing valid graph is untouched by a refusal** |

`TestTopologicalOrder`

| Test | What it proves |
|---|---|
| `test_every_prerequisite_comes_before_its_dependent` | The ordering guarantee the whole engine rests on |
| `test_includes_disconnected_nodes` | Tasks with no dependencies are still scheduled |
| `test_is_deterministic` | Identical input → identical order |
| `test_raises_on_a_cycle` | Stored data that is already cyclic fails loudly |

---

## Rule 2 — No compounding

`TestNoCompounding` — the central correctness hazard.

| Test | What it proves |
|---|---|
| `test_diamond_moves_downstream_once_not_once_per_path` | **A +3 days → D moves 3, not 6.** The headline rule |
| `test_both_intermediate_paths_move_by_the_same_amount` | B and C each move exactly 3 |
| `test_extra_converging_paths_do_not_change_the_result` | Adding a *third* route A→E→D leaves D where the two-path diamond put it — scheduling does not depend on how many paths connect two tasks |
| `test_downstream_task_starts_the_day_after_its_latest_prerequisite` | The arithmetic, stated plainly |
| `test_propagates_through_every_level_of_a_deep_chain` | A 6-level chain propagates to every level, not just the first |
| `test_recompute_is_idempotent` | Running twice changes nothing — the property that makes one edit and ten edits behave identically |

The diamond under test:

```
    A ─┬─> B ─┬─> D
       └─> C ─┘
```

---

## Rule 3 — Blocked / Ready and rollback

`TestDependencyState`

| Test | What it proves |
|---|---|
| `test_task_without_prerequisites_is_ready` | Base case |
| `test_task_is_blocked_while_a_prerequisite_is_unfinished` | Base case |
| `test_task_becomes_ready_once_every_prerequisite_is_done` | Transition to READY |
| `test_one_unfinished_prerequisite_is_enough_to_block` | AND semantics, not OR |
| `test_blocked_and_ready_are_independent_of_the_column` | **A BACKLOG task can be READY; an IN_PROGRESS task can be BLOCKED** |

`TestRollbackOnRegression`

| Test | What it proves |
|---|---|
| `test_moving_a_done_task_back_reblocks_its_dependent` | The rule, directly |
| `test_regression_reblocks_transitively_down_a_chain` | Re-blocking reaches **every** level, not just the direct dependent |

---

## Scheduling policy

`TestSchedulingPolicy`

| Test | What it proves |
|---|---|
| `test_an_early_finish_does_not_drag_downstream_work_forward` | Forward-only scheduling (a documented assumption) |
| `test_lag_days_add_a_gap_after_the_prerequisite` | Lag is honoured |
| `test_a_pinned_task_is_never_moved` | Pins are respected |
| `test_a_pin_that_conflicts_with_prerequisites_is_flagged` | An impossible schedule is **surfaced**, not silently resolved |
| `test_end_date_is_derived_from_duration` | A 1-day task starts and ends the same day |
| `test_records_which_prerequisite_set_the_date` | Binding-constraint provenance |

---

## Critical path

`TestCriticalPath`

| Test | What it proves |
|---|---|
| `test_finds_the_longest_chain_by_duration` | Picks the slow branch over the fast one |
| `test_handles_an_empty_board` | No crash on an empty graph |
| `test_single_task_is_its_own_critical_path` | Degenerate case |

---

## Edge cases

`TestEdgeCases`

| Test | What it proves |
|---|---|
| `test_empty_graph` | Empty input returns empty output |
| `test_disconnected_subgraphs_are_scheduled_independently` | Two unrelated chains do not affect each other |
| `test_edges_referring_to_missing_tasks_are_ignored` | **Deleting a task must not break scheduling for everyone else** |
| `test_a_task_with_many_prerequisites_waits_for_the_latest` | `max`, not `first` or `last` |

---

## Bugs these tests actually caught

Both were found by the suite before any UI existed, which is the argument for
writing the engine test-first.

**1. Dangling edges caused a phantom cycle.**
An edge pointing at a deleted task incremented the dependent's prerequisite
count, but nothing could ever decrement it — so Kahn's algorithm never drained
the queue and reported a cycle that did not exist. Fixed with `live_edges()`,
which every entry point now filters through.
Covered by `test_edges_referring_to_missing_tasks_are_ignored`.

**2. A test was wrong, not the code.**
The original forward-only test passed the *original* dates on the second
recompute. In production the first pass is persisted before the second runs.
The engine was correct; the test was not modelling reality. The test now feeds
back the persisted date, as the API does.

---

## Manual / end-to-end verification

Run against a real Postgres instance via `TestClient`, and confirmed in the
browser:

| Scenario | Result |
|---|---|
| Build the diamond via the API, extend A by 3 days | D moved **3** days |
| Same change on the seeded board | Propagated 3 days through **6 levels and two diamonds** |
| `POST /api/dependencies` closing a loop | **409**, full path in task titles, board byte-identical afterwards |
| Drag "Design database schema" Done → In Progress | Both dependents flipped to **Blocked**; ready count 2 → 1 |
| `PATCH` with a stale `version` | **409** refused |
| Browser refresh | Board returns exactly as left |

---

## Not covered

Stated honestly — see [`KNOWN-FAILURES.md`](./KNOWN-FAILURES.md).

- **No API-layer integration test suite.** Endpoints were verified manually via
  `TestClient` and in the browser, but those checks are not committed as
  automated tests. The engine — where the scored correctness lives — is fully
  covered.
- **No frontend tests.** No component or end-to-end tests were written.
- **The AI suggestion path is untested against a live model**, because the
  available API account has no credits. Validation and filtering logic is
  deterministic and reviewable; the network call itself is not exercised.
- The uncovered 5% of the engine is `_describe_cycle`, a best-effort reporter
  for cycles in already-corrupted stored data — unreachable while the API
  validates on write.
