/**
 * The single owner of board state.
 *
 * Every component reads from here and calls these handlers; none of them talk
 * to the API directly. Centralising it means the optimistic-update rules live
 * in exactly one place, which is what keeps the client and server from drifting.
 *
 * The contract with the server:
 *   - the server is always right about dates and blocked/ready
 *   - a mutation returns EVERY task it moved, so we merge rather than refetch
 *   - if a mutation fails, we restore the snapshot we took before it
 */

import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import { ApiError } from "./api";
import type { Board, Dependency, MutationResult, Task, TaskStatus } from "./types";

export interface Notice {
  kind: "error" | "info";
  message: string;
  /** For a cycle rejection: the chain of task titles that would loop. */
  path?: string[];
}

export function useBoard() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [dependencies, setDependencies] = useState<Dependency[]>([]);
  const [criticalPath, setCriticalPath] = useState<string[]>([]);
  const [overConstrained, setOverConstrained] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<Notice | null>(null);

  const applyBoard = useCallback((board: Board) => {
    setTasks(board.tasks);
    setDependencies(board.dependencies);
    setCriticalPath(board.critical_path);
  }, []);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      applyBoard(await api.getBoard());
      setNotice(null);
    } catch (error) {
      setNotice({
        kind: "error",
        message:
          error instanceof ApiError
            ? error.message
            : "Cannot reach the API. Is the backend running on port 8000?",
      });
    } finally {
      setLoading(false);
    }
  }, [applyBoard]);

  // Load once on mount. Because all state lives on the server, a browser
  // refresh simply runs this again and the board comes back exactly as it was.
  useEffect(() => {
    void load();
  }, [load]);

  /**
   * Merge a mutation result into local state.
   *
   * `affected` contains every task the engine moved - often far more than the
   * one the user touched - so a single edit updates the whole visible cascade.
   */
  const merge = useCallback((result: MutationResult) => {
    setTasks((current) => {
      const byId = new Map(current.map((task) => [task.id, task]));
      for (const task of result.affected) byId.set(task.id, task);
      return [...byId.values()];
    });
    setDependencies(result.dependencies);
    setCriticalPath(result.critical_path);
    setOverConstrained(result.over_constrained_ids);
  }, []);

  /**
   * Run a mutation with an optimistic update.
   *
   * `optimistic` paints the expected result immediately so dragging feels
   * instant. If the server refuses, we put back the snapshot taken before the
   * change - the server never ends up disagreeing with what is on screen.
   */
  const mutate = useCallback(
    async (
      optimistic: (() => void) | null,
      call: () => Promise<MutationResult>,
    ) => {
      const snapshot = { tasks, dependencies, criticalPath };
      optimistic?.();

      try {
        merge(await call());
        setNotice(null);
        return true;
      } catch (error) {
        // Roll back to exactly what was on screen before.
        setTasks(snapshot.tasks);
        setDependencies(snapshot.dependencies);
        setCriticalPath(snapshot.criticalPath);

        if (error instanceof ApiError) {
          const body = error.body as { titles?: string[] } | null;
          setNotice({
            kind: "error",
            message: error.message,
            path: body?.titles,
          });
        } else {
          setNotice({ kind: "error", message: "Something went wrong." });
        }
        return false;
      }
    },
    [tasks, dependencies, criticalPath, merge],
  );

  /**
   * Drag-and-drop between columns.
   *
   * Moving a task to or from Done changes whether its dependents' prerequisites
   * are satisfied, so the server's response re-blocks or unblocks them and the
   * merge above repaints those cards. That is rollback-on-regression, visible.
   */
  const moveTask = useCallback(
    (task: Task, status: TaskStatus, position: number) =>
      mutate(
        () =>
          setTasks((current) =>
            current.map((t) => (t.id === task.id ? { ...t, status, position } : t)),
          ),
        () => api.moveTask(task.id, { version: task.version, status, position }),
      ),
    [mutate],
  );

  const updateTask = useCallback(
    (task: Task, changes: Parameters<typeof api.updateTask>[1]) =>
      mutate(null, () => api.updateTask(task.id, { ...changes, version: task.version })),
    [mutate],
  );

  const createTask = useCallback(
    (input: Parameters<typeof api.createTask>[0]) => mutate(null, () => api.createTask(input)),
    [mutate],
  );

  const deleteTask = useCallback(
    (task: Task) =>
      mutate(
        () => setTasks((current) => current.filter((t) => t.id !== task.id)),
        () => api.deleteTask(task.id),
      ),
    [mutate],
  );

  /**
   * Add a prerequisite.
   *
   * No optimistic update here on purpose: whether this edge is legal is a
   * question only the engine can answer, so we wait rather than draw a line
   * that might have to be erased.
   */
  const addDependency = useCallback(
    (upstreamId: string, downstreamId: string) =>
      mutate(null, () =>
        api.addDependency({ upstream_id: upstreamId, downstream_id: downstreamId }),
      ),
    [mutate],
  );

  const removeDependency = useCallback(
    (id: string) => mutate(null, () => api.removeDependency(id)),
    [mutate],
  );

  return {
    tasks,
    dependencies,
    criticalPath,
    overConstrained,
    loading,
    notice,
    setNotice,
    reload: load,
    moveTask,
    updateTask,
    createTask,
    deleteTask,
    addDependency,
    removeDependency,
  };
}
