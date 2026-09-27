/**
 * TypeScript mirrors of the backend's Pydantic schemas.
 *
 * Keeping these in sync by hand is a deliberate trade-off for a 72-hour build:
 * a generated client from the OpenAPI schema would be the production answer,
 * and that is recorded in docs/KNOWN-FAILURES.md.
 */

/** The four Kanban columns. Workflow stage only - see DependencyState. */
export type TaskStatus = "BACKLOG" | "IN_PROGRESS" | "REVIEW" | "DONE";

/**
 * Whether a task's prerequisites are satisfied. Computed by the engine on the
 * server, never by this client. Orthogonal to the column: a BACKLOG task can be
 * READY and an IN_PROGRESS task can be BLOCKED.
 */
export type DependencyState = "READY" | "BLOCKED";

export type DependencyOrigin = "HUMAN" | "AI_ACCEPTED";

export interface Task {
  id: string;
  title: string;
  description: string;
  status: TaskStatus;
  /** ISO date, e.g. "2026-09-21". Written by the engine. */
  start_date: string;
  /** ISO date. Derived from start_date + duration_days, never edited directly. */
  end_date: string;
  duration_days: number;
  pinned: boolean;
  position: number;
  dependency_state: DependencyState;
  /** Increments on every write. Sent back on edits so stale writes are refused. */
  version: number;
  /** Which prerequisite decided this task's start date, if any. */
  binding_constraint_id: string | null;
}

export interface Dependency {
  id: string;
  upstream_id: string;
  downstream_id: string;
  lag_days: number;
  origin: DependencyOrigin;
}

export interface Board {
  tasks: Task[];
  dependencies: Dependency[];
  critical_path: string[];
}

/**
 * What every mutating endpoint returns.
 *
 * `affected` is every task the edit moved, not just the one that was touched,
 * so one round trip carries the whole cascade.
 */
export interface MutationResult {
  affected: Task[];
  dependencies: Dependency[];
  critical_path: string[];
  over_constrained_ids: string[];
}

/** The 409 body when a dependency would close a loop. */
export interface CycleError {
  detail: string;
  path: string[];
  titles: string[];
}

export const COLUMNS: { id: TaskStatus; label: string }[] = [
  { id: "BACKLOG", label: "Backlog" },
  { id: "IN_PROGRESS", label: "In Progress" },
  { id: "REVIEW", label: "Review" },
  { id: "DONE", label: "Done" },
];

/** An LLM-proposed edge awaiting human review. Not part of the graph yet. */
export interface Suggestion {
  id: string;
  upstream_id: string;
  downstream_id: string;
  /** Model's self-reported confidence, 0..1. Shown, never used to auto-accept. */
  confidence: number;
  /** Must quote evidence from the task text. */
  rationale: string;
  status: "PENDING" | "ACCEPTED" | "REJECTED";
}

export interface SuggestionList {
  ai_enabled: boolean;
  suggestions: Suggestion[];
  /** Distinguishes "the call failed" from "the model found nothing". */
  error: string | null;
}

/** One task a hypothetical edit would move (the dry-run preview). */
export interface PreviewedMove {
  id: string;
  title: string;
  from_start: string;
  to_start: string;
  shift_days: number;
}

export interface PreviewResult {
  moves: PreviewedMove[];
}
