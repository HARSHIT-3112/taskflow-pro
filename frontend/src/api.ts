/**
 * Typed HTTP client. One function per endpoint, and the ONLY place in the
 * frontend that knows a server exists - components call these functions and
 * never touch fetch directly.
 */

import type { Board, MutationResult, TaskStatus } from "./types";

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

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
