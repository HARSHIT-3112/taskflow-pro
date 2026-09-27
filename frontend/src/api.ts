/**
 * Typed HTTP client. One function per endpoint, and the ONLY place in the
 * frontend that knows a server exists - components call these functions and
 * never touch fetch directly.
 */

import type {
  Board,
  MutationResult,
  PreviewResult,
  SuggestionList,
  TaskStatus,
} from "./types";

/**
 * Where the API lives.
 *
 * In a production build the API is served from the same origin (see
 * vercel.json), so an empty base makes every call relative - "/api/board" -
 * which means no CORS preflight and nothing to configure per environment.
 *
 * In development the Vite server and the API are separate origins, so we point
 * at the documented local port. VITE_API_URL overrides either case.
 */
const BASE =
  import.meta.env.VITE_API_URL ?? (import.meta.env.PROD ? "" : "http://localhost:8000");

/**
 * Thrown for any non-2xx response.
 *
 * `body` carries the server's parsed error, so a caller can look inside it -
 * that is how the cycle rejection's `path` and `titles` reach the UI and
 * produce a readable message instead of "409".
 */
export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (!response.ok) {
    let body: unknown = null;
    let message = `Request failed (${response.status})`;

    try {
      body = await response.json();
      const detail = (body as { detail?: unknown })?.detail;
      // FastAPI puts the message in `detail`, which is a string for simple
      // errors and an object for the structured cycle rejection.
      if (typeof detail === "string") {
        message = detail;
      } else if (detail && typeof detail === "object" && "detail" in detail) {
        message = String((detail as { detail: unknown }).detail);
        body = detail;
      }
    } catch {
      // Response had no JSON body; keep the generic message.
    }

    throw new ApiError(response.status, message, body);
  }

  return (await response.json()) as T;
}

/** Load the whole board. Called on startup and after a refresh. */
export const getBoard = () => request<Board>("/api/board");

export const createTask = (input: {
  title: string;
  description?: string;
  status?: TaskStatus;
  start_date: string;
  duration_days?: number;
}) =>
  request<MutationResult>("/api/tasks", {
    method: "POST",
    body: JSON.stringify(input),
  });

/**
 * Edit a task. `version` is what the client was looking at; the server refuses
 * the write if someone else changed the task first.
 */
export const updateTask = (
  id: string,
  input: {
    version: number;
    title?: string;
    description?: string;
    status?: TaskStatus;
    start_date?: string;
    duration_days?: number;
    pinned?: boolean;
  },
) =>
  request<MutationResult>(`/api/tasks/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });

/** Drag-and-drop: move a task to a column at a given fractional position. */
export const moveTask = (
  id: string,
  input: { version: number; status: TaskStatus; position: number },
) =>
  request<MutationResult>(`/api/tasks/${id}/move`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });

export const deleteTask = (id: string) =>
  request<MutationResult>(`/api/tasks/${id}`, { method: "DELETE" });

/** Add a prerequisite. Throws ApiError(409) with the path if it would cycle. */
export const addDependency = (input: {
  upstream_id: string;
  downstream_id: string;
  lag_days?: number;
}) =>
  request<MutationResult>("/api/dependencies", {
    method: "POST",
    body: JSON.stringify(input),
  });

export const removeDependency = (id: string) =>
  request<MutationResult>(`/api/dependencies/${id}`, { method: "DELETE" });

// --- AI suggestions ---------------------------------------------------------

export const getSuggestions = () => request<SuggestionList>("/api/suggestions");

export const generateSuggestions = () =>
  request<SuggestionList>("/api/suggestions/generate", { method: "POST" });

/** Accepting routes through the same validated endpoint a manual edge uses. */
export const acceptSuggestion = (id: string) =>
  request<MutationResult>(`/api/suggestions/${id}/accept`, { method: "POST" });

export const rejectSuggestion = (id: string) =>
  request<SuggestionList>(`/api/suggestions/${id}/reject`, { method: "POST" });

/**
 * Score an edit without saving it.
 *
 * Nothing is written server-side, so this is safe to call while the user is
 * still typing.
 */
export const previewTask = (
  id: string,
  input: { start_date?: string; duration_days?: number },
) =>
  request<PreviewResult>(`/api/tasks/${id}/preview`, {
    method: "POST",
    body: JSON.stringify(input),
  });
