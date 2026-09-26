# TaskFlow Pro — Design Document

Architecture, data model, and known limitations.

---

## 1. The problem in one paragraph

Kanban tools treat tasks as independent cards, but real work is a dependency
graph. The ordering knowledge exists — it just lives in a lead's head or a
stale spreadsheet. The failure is silent: a schema task slips two days, nobody
re-dates the API task behind it, and the slip surfaces at the deadline instead
of on the day it happened. TaskFlow Pro puts a DAG engine behind the board so
the system answers two questions continuously: **what is actually workable
right now**, and **if this task moves, what else moves**.

---

## 2. Architecture

### 2.1 The governing invariant

> **The engine alone decides dates and dependency state. The board never
> computes — it renders what the engine returns.**

Every design decision below follows from that sentence.

### 2.2 Layers

```
┌─────────────────────────────────────────────┐
│  frontend/          React + TypeScript      │
│    useBoard.ts      all state, all API calls│
│    components/      render only             │
└──────────────────┬──────────────────────────┘
                   │ HTTP (intent-based)
┌──────────────────▼──────────────────────────┐
│  backend/app/api/          FastAPI routes   │
│    validate, transact, return the cascade   │
├─────────────────────────────────────────────┤
│  backend/app/services/     the bridge       │
│    scheduler.py    DB rows ⇄ engine types   │
│    suggestions.py  LLM proposals            │
├─────────────────────────────────────────────┤
│  backend/app/engine/       PURE             │
│    graph.py        no DB, no HTTP, no       │
│                    framework imports        │
├─────────────────────────────────────────────┤
│  backend/app/models.py     SQLModel tables  │
│  PostgreSQL 16                              │
└─────────────────────────────────────────────┘

backend/app/domain.py — enums and value types.
Imports only the standard library; both the engine and the DB layer depend on it.
```

**Why the engine is pure.** It imports nothing but the standard library and
`domain.py`. That is not aesthetic: it means the entire engine is testable in
milliseconds without a database, so the hard cases (diamonds, deep chains,
regression) can be proven in unit tests rather than clicked through a UI. It
also means the engine cannot be bypassed by a convenient shortcut in a route
handler.

### 2.3 Request flow

Every mutating request follows the same five steps:

1. **Load and lock** the affected rows (`SELECT … FOR UPDATE`)
2. **Validate** — including the optimistic-concurrency `version` check
3. **Apply** the change
4. **Recompute** the whole board through the engine
5. **Commit once**

If any step raises, nothing commits. Each mutation returns **every task the
change moved**, so the client reconciles the full cascade in one round trip.

---

## 3. The four rules, and how each is enforced

### 3.1 No cycles

Adding `U → V` closes a loop **exactly when V can already reach U**. So the
engine searches forward from V; arriving at U means refuse.

```python
find_cycle_path(node_ids, edges, new_upstream_id, new_downstream_id)
    -> ["A", "B", "C", "A"]  |  None
```

Breadth-first, so the path returned is the shortest — which makes a readable
error. The API converts ids to titles:

> This dependency would create a circular relationship: Design database
> schema → Build REST API endpoints → Write integration tests → Deploy to
> staging → Run load testing → Production release → Design database schema

**The check runs inside the same transaction that would have written the edge**,
holding row locks. Nothing is written, and a concurrent insert cannot slip a
second edge in between the check and the write.

### 3.2 No compounding — the central hazard

The naive approach propagates deltas along edges: *"A moved +3, push everything
downstream by +3."* In a diamond (A feeds B and C; both feed D) the +3 arrives
at D twice and D moves **+6**. Patching that with a visited-set only papers
over the wrong model.

**This engine never looks at deltas.** It walks the graph in topological order
(Kahn's algorithm) and recalculates each task's **absolute** start date:

```
start = max(over all prerequisites: prerequisite.end + 1 + lag)
end   = start + duration − 1
```

Every node is evaluated **exactly once**, no matter how many paths reach it.
Double counting is not prevented — **it is inexpressible**.

Two properties fall out:

- **Idempotence** — running recompute twice changes nothing, because it
  computes an absolute position rather than applying a relative shift. One edit
  and ten edits therefore behave identically.
- **Forward-only scheduling** — `max(node.start_date, earliest_allowed)` means
  a task never moves *earlier* than where it already sits. An upstream slip
  pushes work later; an upstream finishing early leaves the slack visible
  rather than silently dragging the team's plan forward. This is a deliberate
  policy choice, documented in §6.

### 3.3 Rollback on regression

`BLOCKED`/`READY` is **derived on every recompute** from live prerequisite
status, and never read back as an input:

```python
for edge in prerequisites:
    if upstream.status is not TaskStatus.DONE:
        return DependencyState.BLOCKED
return DependencyState.READY
```

Dragging a task from Done back to In Progress therefore re-blocks its
dependents — transitively — with **zero lines of rollback code**. There is
nothing to un-flip, because nothing was ever latched.

This is the clearest example of the project's general principle: *derive
everything you can; store only what you cannot compute.*

### 3.4 Persistence

All state lives in PostgreSQL. A browser refresh re-runs `GET /api/board` and
the plan returns exactly as it was — including board position, which is stored
per task.

---

## 4. Data model

Three tables. The dependency graph **is** the `dependency` table: one row per
directed edge.

### 4.1 `task`

| Column | Type | Notes |
|---|---|---|
| `id` | str (uuid4 hex) | PK |
| `title`, `description` | str | Also the input to the AI suggestion feature |
| `status` | enum | BACKLOG / IN_PROGRESS / REVIEW / DONE — **workflow stage only** |
| `start_date` | date | Written by the engine |
| `end_date` | date | **Derived**: `start + duration − 1` |
| `duration_days` | int ≥ 1 | **The source of truth for length** |
| `pinned` | bool | Engine will not move this task at all |
| `position` | float | Fractional rank within a column |
| `dependency_state` | enum | READY / BLOCKED — **a cache, never an input** |
| `version` | int | Optimistic concurrency |
| `binding_constraint_id` | str? | Which prerequisite set this start date |

Three decisions worth defending:

- **`duration_days` is truth, `end_date` is derived.** If both were editable
  they could contradict each other. Removing that possibility removes a class
  of bugs.
- **`position` is a float.** A card dropped between `1.0` and `2.0` takes
  `1.5` — one row written, never a column renumber.
- **`dependency_state` is denormalised, not authoritative.** The engine writes
  it so the board renders without recomputing. Nothing reads it to make a
  decision. Delete the column, recompute from the graph, and you get identical
  answers.

### 4.2 `dependency`

| Column | Notes |
|---|---|
| `upstream_id`, `downstream_id` | FK → task, **both indexed**, `ON DELETE CASCADE` |
| `lag_days` | Extra gap required after the prerequisite ends |
| `origin` | `HUMAN` or `AI_ACCEPTED` — provenance |

Both ends are indexed because the graph is walked in both directions: forwards
("what depends on this?") and backwards ("what does this wait on?").

**Constraints:** `UNIQUE(upstream_id, downstream_id)` stops duplicate edges;
a self-loop is rejected in the API. **Acyclicity is not expressible as a row
constraint**, so the engine enforces it inside the transaction — that boundary
is deliberate and documented rather than accidental.

### 4.3 `suggestion`

An LLM-proposed edge awaiting review: the pair, `confidence`, `rationale`, and
`status` (PENDING / ACCEPTED / REJECTED).

**Deliberately a separate table from `dependency`.** A suggestion is not part of
the graph until a human accepts it and it passes the cycle check. The model has
no write path into the graph at all.

---

## 5. AI usage

The model **proposes**; the engine **disposes**.

```
board text ──> LLM (Gemini or Claude, structured output)
                   │
                   ▼  candidate edges
        ┌──────────────────────────────┐
        │ 1. closed-world prompt       │  only ids we sent
        │ 2. schema-enforced output    │  messages.parse(), Pydantic
        │ 3. allowlist validation      │  unknown / self / dup / rejected
        │ 4. ENGINE cycle check        │  illegal edges never displayed
        │ 5. confidence floor (0.55)   │
        └──────────────┬───────────────┘
                       ▼
              human review drawer
                       │ accept
                       ▼
        create_edge()  ← the SAME function manual edges use
```

**Structural guarantee:** there is no endpoint that writes a dependency from a
suggestion. `POST /api/suggestions/{id}/accept` calls `create_edge()` — the same
function `POST /api/dependencies` uses — so an accepted suggestion passes cycle
detection exactly like human input.

**Prompt grounding.** The system prompt states that inventing an id is a
failure, that the rationale must quote evidence from the task text, and that
**returning an empty list is a correct answer**. Only `id`, `title` and
`description` are sent — dates and statuses are irrelevant to whether one task
is a prerequisite of another, and sending less gives the model less to be
distracted by.

**Provider independence.** `app/services/llm.py` is the only file that knows
which vendor answers. It exposes one function — given a prompt and a Pydantic
schema, return instances of that schema or an error string — and both Gemini
and Claude implement it. Every layer above is identical either way, which is
the argument that the grounding is *structural* rather than vendor-specific.

**Calibration note.** The synopsis proposed a low temperature. Gemini honours
`temperature=0.2`. Claude Opus 5 removed the sampling parameters entirely
(sending `temperature` returns a 400), so on that path determinism comes from
structured outputs and the confidence floor alone.

**Graceful degradation.** With no API key the board is fully functional and the
panel explains why suggestions are unavailable. Failures are reported
*distinctly* from "the model found nothing" — an empty list means very
different things in those two cases.

---

## 6. Key assumptions

Stated so a reviewer can disagree with them precisely.

1. **Day granularity, stored as UTC dates.** No timezone drift.
2. **Duration is the source of truth**; `end_date` is derived and never edited
   directly.
3. **Scheduling is forward-only.** Upstream slips push downstream, but an early
   finish does not pull work earlier without an explicit action — surprising a
   team by moving work earlier is worse than leaving slack visible.
4. **A pinned task is frozen.** If prerequisites would push it later, the engine
   honours the pin and flags the task `over_constrained` rather than producing
   an impossible schedule.
5. **Calendar days by default**, with a working-day flag as future work.
6. **Single shared board**, no per-user permissions, no authentication.
7. **A one-day task starts and ends on the same day** (`end = start + n − 1`).

---

## 7. Known limitations

See [`KNOWN-FAILURES.md`](./KNOWN-FAILURES.md) for the full list with
reproduction steps. In summary:

- No migration tool — tables are created from the models at startup
- No authentication or multi-user permissions
- No realtime sync between browsers (optimistic concurrency detects conflicts;
  it does not push updates)
- Whole-board recompute rather than the transitive closure of the change
- No resource levelling, so the critical path is duration-based only
- Frontend types are hand-mirrored from the backend schemas
- Suggestion quality degrades on terse task titles

---

## 8. Where this goes at scale

The board is loaded whole on every mutation, which is exact and simple at tens
to hundreds of tasks. Past a few thousand, the same **unchanged** engine would
run against a cached in-memory graph, with recompute moved to a background
worker and results pushed over websockets, and recompute narrowed to the
transitive closure of the changed node.

None of that requires rewriting the scheduling logic — which is the practical
payoff of having kept the engine free of infrastructure dependencies.
