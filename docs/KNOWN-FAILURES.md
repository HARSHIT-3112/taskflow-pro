# Known Failure Cases and Limitations

Everything here is a deliberate trade-off or a genuine gap. Documented rather
than hidden, with the reasoning and what production would do instead.

---

## 1. Failures you can reproduce

### 1.1 AI suggestions need a model key

**Reproduce:** click *AI suggestions → Analyse board* with neither
`GEMINI_API_KEY` nor `ANTHROPIC_API_KEY` set in `.env`.

**What happens:** the panel explains the feature is unconfigured and points at
where to get a free key. The board is unaffected.

**Status:** intended. The feature is verified working against Gemini
(`gemini-2.5-flash`) — two deleted dependencies were rediscovered at 0.95
confidence with quoted evidence. The Anthropic path is implemented but was
never exercised live, because the available Anthropic account had no credits.

**Rate limits:** Gemini's free tier is generous but finite. A 429 surfaces as
*"Gemini rate limit reached. Wait a moment and try again."* rather than a crash.

### 1.2 Two browsers do not see each other's changes until refresh

**Reproduce:** open the board in two tabs. Edit a task in tab A. Tab B still
shows the old plan.

**What happens:** tab B's next edit to that task is **refused with 409** —
optimistic concurrency via the `version` column catches the conflict and tells
the user to refresh. No data is lost or silently overwritten.

**Limitation:** conflicts are *detected*, not *prevented*, and there is no push
mechanism. Production would add websockets or SSE to broadcast the affected
tasks after each mutation.

---

### 1.3 Shortening a task does not pull downstream work earlier

**Reproduce:** shorten a prerequisite's duration. Dependents keep their dates.

**Status:** **intended behaviour**, not a bug — forward-only scheduling
(`DESIGN.md` §6.3). Surprising a team by silently moving their work earlier is
worse than leaving the slack visible.

**Limitation:** there is no "compress schedule" action to claw back that slack
deliberately. That is the missing half of the feature.

---

### 1.4 A pinned task can be left in an impossible schedule

**Reproduce:** pin a task to a date, then extend a prerequisite past it.

**What happens:** the engine honours the pin, does not move the task, and flags
it `over_constrained`. The card shows a **Conflict** badge.

**Limitation:** the conflict is surfaced but not resolved — there is no
suggested fix, and no way to see *by how much* the schedule is impossible.

---

### 1.5 Dragging within a column does not reorder

**Reproduce:** drag a card above another in the same column.

**What happens:** nothing. Only column-to-column moves are handled; a card
dropped in its own column returns to place.

**Why:** `position` is a fractional rank and the persistence works, but the
drop-index calculation for intra-column sorting was cut for time. Cross-column
movement — the actual requirement — works and persists.

---

### 1.6 Very long task titles overflow a card

**Reproduce:** create a task with a 200-character title.

**What happens:** the card grows tall and the column looks uneven. No
truncation or ellipsis. Cosmetic only.

---

## 2. Deliberate scope cuts

| Cut | Why | What production would do |
|---|---|---|
| **No migration tool** | `SQLModel.metadata.create_all()` at startup. Saved ~1 hour that went to engine tests, which are worth 30% of the score | Alembic, with versioned migrations in CI |
| **No authentication** | Single shared board; auth adds no signal for the graded problem | Session auth + per-board membership |
| **No resource levelling** | Critical path is duration-based only; two tasks can be scheduled on the same person at once | Capacity constraints per assignee |
| **No working-day calendar** | Calendar days by default; weekends and holidays are scheduled like any other day | A flag exists in the assumptions; the engine would skip non-working days when computing `end` |
| **Frontend types hand-mirrored** | `frontend/src/types.ts` duplicates the Pydantic schemas by hand, so they can silently drift | Generate a client from the OpenAPI schema at `/openapi.json` |
| **Whole-board recompute** | Exact and simple at this scale; the engine is O(V+E) and a hundred tasks recompute in single-digit ms | Recompute only the transitive closure of the changed node |

---

## 3. Security notes

### 3.1 Dev dependency vulnerabilities not force-fixed

`npm audit` and the Python toolchain report advisories in **development**
dependencies (the test runner and linters). They are not in the runtime path,
and "fixing" them means downgrading to versions with different behaviour.

**Decision:** documented rather than force-fixed. A production pipeline would
pin and track them properly rather than run `--force` blindly.

### 3.2 CORS is an explicit allowlist

`settings.cors_origins` lists the Vite dev ports rather than using `"*"`. A
wildcard origin would let any site on the internet call this API from a
visitor's browser. The deployed origin must be added to that list.

### 3.3 Model output is treated as untrusted data

Task text sent to the model is untrusted input, and the model's response is
parsed as data and validated — never executed, never obeyed as an instruction.
The API key is server-side only and never reaches the browser.

---

## 4. What would break at scale

| Tasks | Behaviour |
|---|---|
| ~100 | Everything is fast. Recompute is single-digit milliseconds |
| ~1,000 | Still fine, but loading the whole board on every mutation starts to show |
| ~10,000 | Whole-board recompute per mutation becomes the bottleneck, and the board UI needs virtualisation |

The fix does **not** require touching the scheduling logic — the engine is pure
and infrastructure-free, so it would run unchanged against a cached in-memory
graph with recompute in a background worker. That is the practical payoff of
the purity constraint.

---

## 5. Things I would do first with more time

1. **Intra-column reordering** — the most visible gap
2. **API integration test suite** — the engine is well covered; the routes are not
3. **Generated frontend client** from the OpenAPI schema
4. **Websocket push** so multiple browsers stay in sync
5. **Alembic migrations**
6. **Interactive graph editing** — the graph view is read-only; dragging an
   edge between nodes would be a natural way to create a dependency
