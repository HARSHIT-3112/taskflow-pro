/**
 * Tests for the board state hook.
 *
 * This is where the client's contract with the server lives, so these cover
 * the three rules that keep the two from drifting:
 *
 *   - a mutation's `affected` list is MERGED, not replaced
 *   - a failed mutation restores exactly what was on screen before
 *   - a cycle rejection surfaces the offending path as a readable notice
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { ApiError } from "./api";
import type { Board, MutationResult, Task } from "./types";
import { useBoard } from "./useBoard";

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    getBoard: vi.fn(),
    moveTask: vi.fn(),
    updateTask: vi.fn(),
    addDependency: vi.fn(),
  };
});

function makeTask(id: string, overrides: Partial<Task> = {}): Task {
  return {
    id,
    title: `Task ${id}`,
    description: "",
    status: "BACKLOG",
    start_date: "2026-09-21",
    end_date: "2026-09-21",
    duration_days: 1,
    pinned: false,
    position: 1,
    dependency_state: "READY",
    version: 0,
    binding_constraint_id: null,
    ...overrides,
  };
}

const initialBoard: Board = {
  tasks: [makeTask("a"), makeTask("b"), makeTask("c")],
  dependencies: [],
  critical_path: [],
};

beforeEach(() => {
  vi.mocked(api.getBoard).mockResolvedValue(structuredClone(initialBoard));
});

afterEach(() => {
  vi.clearAllMocks();
});

async function mountBoard() {
  const view = renderHook(() => useBoard());
  await waitFor(() => expect(view.result.current.loading).toBe(false));
  return view;
}

describe("useBoard", () => {
  it("loads the board on mount", async () => {
    const { result } = await mountBoard();
    expect(result.current.tasks).toHaveLength(3);
  });

  it("merges `affected` rather than replacing the board", async () => {
    // The server returns only what it moved. Replacing the list with that
    // would make every untouched task vanish.
    const { result } = await mountBoard();

    const mutation: MutationResult = {
      affected: [makeTask("b", { start_date: "2026-10-01", version: 1 })],
      dependencies: [],
      critical_path: [],
      over_constrained_ids: [],
    };
    vi.mocked(api.moveTask).mockResolvedValue(mutation);

    await act(async () => {
      await result.current.moveTask(result.current.tasks[1], "IN_PROGRESS", 2);
    });

    expect(result.current.tasks).toHaveLength(3);
    const b = result.current.tasks.find((t) => t.id === "b")!;
    expect(b.start_date).toBe("2026-10-01");
  });

  it("applies a move optimistically before the server replies", async () => {
    const { result } = await mountBoard();

    let release!: (value: MutationResult) => void;
    vi.mocked(api.moveTask).mockReturnValue(
      new Promise<MutationResult>((resolve) => {
        release = resolve;
      }),
    );

    act(() => {
      void result.current.moveTask(result.current.tasks[0], "REVIEW", 5);
    });

    // Painted immediately, while the request is still in flight.
    await waitFor(() =>
      expect(result.current.tasks.find((t) => t.id === "a")!.status).toBe("REVIEW"),
    );

    await act(async () => {
      release({
        affected: [],
        dependencies: [],
        critical_path: [],
        over_constrained_ids: [],
      });
    });
  });

  it("rolls back to the previous state when the server refuses", async () => {
    const { result } = await mountBoard();
    const before = result.current.tasks.find((t) => t.id === "a")!.status;

    vi.mocked(api.moveTask).mockRejectedValue(
      new ApiError(409, "This task was changed by someone else", null),
    );

    await act(async () => {
      await result.current.moveTask(result.current.tasks[0], "DONE", 9);
    });

    expect(result.current.tasks.find((t) => t.id === "a")!.status).toBe(before);
    expect(result.current.notice?.kind).toBe("error");
  });

  it("surfaces the circular path when a dependency is refused", async () => {
    const { result } = await mountBoard();

    vi.mocked(api.addDependency).mockRejectedValue(
      new ApiError(409, "This dependency would create a circular relationship", {
        titles: ["Task a", "Task b", "Task a"],
      }),
    );

    await act(async () => {
      await result.current.addDependency("c", "a");
    });

    expect(result.current.notice?.message).toMatch(/circular/i);
    expect(result.current.notice?.path).toEqual(["Task a", "Task b", "Task a"]);
  });

  it("reports an unreachable API instead of rendering an empty board", async () => {
    vi.mocked(api.getBoard).mockRejectedValue(new Error("network down"));

    const { result } = renderHook(() => useBoard());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.notice?.kind).toBe("error");
    expect(result.current.notice?.message).toMatch(/backend/i);
  });
});
