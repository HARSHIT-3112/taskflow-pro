# TaskFlow Pro

A Kanban board where a **DAG engine** decides which tasks are ready, which are
blocked, and how a schedule change cascades.

Most project tools treat tasks as independent cards. Real work is a dependency
graph — integration tests cannot run until the API and the schema are done. This
puts that graph behind the board and keeps it correct under continuous editing.

---

## Quick start

**Requirements:** Docker, Python 3.12+, Node 20+.

```bash
git clone https://github.com/HARSHIT-3112/taskflow-pro.git
cd taskflow-pro
make setup      # starts Postgres, installs deps, creates tables, seeds the board
make backend    # API on http://localhost:8000  (docs at /docs)
```

Then in a second terminal:

```bash
make frontend   # board on http://localhost:5173
```

`make help` lists every command.

> **AI suggestions are optional.** Without a model key the board is fully
> functional and the suggestion panel explains why the feature is unavailable.
> To enable it, put a **free** Gemini key from
> [aistudio.google.com/apikey](https://aistudio.google.com/apikey) into `.env`
> as `GEMINI_API_KEY` and restart the API. An `ANTHROPIC_API_KEY` works too —
> see [`.env.example`](.env.example).

---

## See the four rules in 60 seconds

The seeded project is deliberately shaped so every rule is demonstrable with no
setup. Two diamonds, a six-level chain, and a task already Done with dependents.

| # | Do this | Expected |
|---|---|---|
| 1 | **No compounding** — open *Design database schema*, change duration `3` → `6`, Save | *Write integration tests* moves **3 days, not 6** — even though two paths reach it. *Production release* also moves exactly 3, six levels away |
| 2 | **No cycles** — open *Design database schema*, add prerequisite *Production release* | Refused, with the full circular path named in task titles. The board is unchanged |
| 3 | **Rollback** — drag *Design database schema* from Done to In Progress | *Build REST API endpoints* and *Build authentication service* turn **Blocked**, showing what they are waiting on |
| 4 | **Persistence** — refresh the browser | Everything stays exactly as you left it |
| 5 | **See the graph** — click *Dependency graph* | The DAG drawn by topological depth: the diamond, the six-level chain, and the critical path highlighted. Click any node to open it |

Run `make seed` at any time to reset the board.

---

## Architecture

```
frontend/   React + TypeScript + Vite     board, drag-and-drop, review drawer
backend/
  app/api/        FastAPI routes          validate → transact → return cascade
  app/services/   the bridge              DB rows ⇄ engine types; LLM proposals
  app/engine/     PURE dependency engine  no DB, no HTTP, no framework imports
  app/domain.py   enums + value types     standard library only
  app/models.py   SQLModel tables
  tests/          36 invariant tests
docs/       DESIGN.md · TEST-CASES.md · KNOWN-FAILURES.md
```

**The governing invariant:** the engine alone decides dates and dependency
state; the board never computes, it renders what the engine returns.

**Stack:** FastAPI · SQLModel · PostgreSQL 16 · React · TypeScript · dnd-kit ·
pytest · ruff. The interface is hand-written CSS with design tokens — no UI
framework — and follows the system light/dark preference.

Full detail in [`docs/DESIGN.md`](docs/DESIGN.md).

---

## How the hard part works

**No compounding.** The naive approach propagates deltas along edges — in a
diamond the delay arrives twice and the downstream task moves **+6** instead of
+3. This engine never looks at deltas. It walks the graph in topological order
(Kahn's algorithm) and recalculates **absolute** dates:

```
start = max(over prerequisites: prerequisite.end + 1 + lag)
```

Every node is evaluated exactly once, however many paths reach it. **Double
counting is not prevented — it is inexpressible.** Recompute is also idempotent,
so one edit and ten edits behave identically.

**Rollback needs no rollback code.** `BLOCKED`/`READY` is derived from live
prerequisite status on every recompute and never read back as an input. Moving a
task out of Done just recomputes, and the dependents re-block transitively. The
state was never latched, so there is nothing to un-latch.

**Cycles are refused before they are written.** Adding `U → V` closes a loop
exactly when V can already reach U. The check runs inside the same transaction
that would have written the edge, holding row locks, so a refusal leaves the
stored graph untouched.

---

## Testing

```bash
make test    # 63 tests — 95% coverage of the engine, 79% overall
make lint
```

The engine imports nothing but the standard library, so the whole suite runs in
~50 ms without a database. That speed is why the hard cases — diamonds, deep
chains, regression, idempotence, pinning — are covered exhaustively rather than
spot-checked through the UI.

Full list, plus the two real bugs the tests caught, in
[`docs/TEST-CASES.md`](docs/TEST-CASES.md).

---

## AI / LLM usage

**The model proposes; the engine disposes.**

The model reads task titles and descriptions and proposes likely prerequisites.
**Either Gemini (`gemini-2.5-flash`) or Claude (`claude-opus-5`) can answer** —
provider selection lives in `app/services/llm.py` and nothing else in the
feature depends on it. Every suggestion passes five filters before a human ever
sees it:

1. **Closed-world prompt** — may reference only ids we sent; inventing one is
   defined as failure, and returning an empty list is stated to be correct
2. **Schema-enforced output** — `messages.parse()` against a Pydantic model
3. **Allowlist validation** — unknown ids, self-edges, duplicates and
   previously-rejected pairs discarded
4. **The engine's cycle check** — an illegal suggestion is never displayed
5. **Confidence floor**, with the rationale shown so the reviewer judges the
   reasoning rather than a number

The structural guarantee: **there is no endpoint that writes a dependency from a
suggestion.** Accepting calls `create_edge()` — the same function manual edge
creation uses — so an AI-originated edge passes exactly the same validation.
Accepted edges are stored with `origin = AI_ACCEPTED` so the graph stays
auditable.

**Verified end to end.** Two real dependencies were deleted from the seeded
board and the model rediscovered both at 0.95 confidence, quoting the evidence
from the task descriptions:

> *"The 'Write integration tests' task \"Cannot run until both the REST
> endpoints and the authentication service are working\"."*

That the same validation pipeline works unchanged across two different vendors
is the point: **the safety properties are structural, not a property of any
particular model.**

---

## Key assumptions

1. **Day granularity, UTC dates.** No timezone drift.
2. **Duration is the source of truth**; `end_date` is derived and never edited
   directly. A one-day task starts and ends on the same day.
3. **Scheduling is forward-only.** Upstream slips push downstream; an early
   finish leaves the slack visible rather than silently pulling work earlier.
4. **A pinned task is frozen.** If prerequisites would push it later, the engine
   honours the pin and flags the task as over-constrained rather than producing
   an impossible schedule.
5. **Calendar days**, not working days.
6. **Single shared board**, no authentication or per-user permissions.
7. **`BLOCKED`/`READY` is orthogonal to the column** — a Backlog task can be
   Ready, and an In Progress task can be Blocked.

---

## Limitations

The honest short list. Full detail, with reproduction steps, in
[`docs/KNOWN-FAILURES.md`](docs/KNOWN-FAILURES.md).

- **Reordering is "insert above"** — dropping on a card puts the dragged card
  directly above it; there is no dedicated drop-at-end target
- **No realtime sync.** Conflicts between two browsers are *detected* (409 via
  optimistic concurrency) but not pushed
- **No "compress schedule" action** to reclaim slack after an early finish
- **No migration tool** — tables are created from the models at startup
- **No authentication**, no resource levelling, no working-day calendar
- **Frontend types are hand-mirrored** from the backend schemas and can drift
- **No frontend automated tests** — the board was verified manually in a browser
- **Concurrency is reasoned about, not tested** — the API suite runs on SQLite,
  which accepts row-lock syntax without actually locking

---

## AI-Tool Declaration

AI tools were used during development, and the solution itself uses an LLM.

**In the product:** an LLM powers the dependency suggestion feature described
above, server-side only. Google Gemini (`gemini-2.5-flash`, via `google-genai`)
and Anthropic Claude (`claude-opus-5`, via `anthropic`) are both supported;
whichever key is configured is used.

**In the build:** Claude Code was used as a coding assistant throughout —
scaffolding, implementation, tests, and documentation. Architectural decisions
(pure engine, absolute recompute over delta propagation, derived dependency
state, suggestions as a separate table) were reviewed and are defended in
[`docs/DESIGN.md`](docs/DESIGN.md). All code was read and verified; the test
suite exists precisely so the behaviour is demonstrable rather than assumed.

---

## Licence

Built for the Contata Solutions hackathon, September 2026.
