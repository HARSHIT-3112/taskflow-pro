/**
 * Top-level component: the board, the detail panel, and the notice bar.
 *
 * All state lives in useBoard(). This file wires drag-and-drop to it and
 * decides what is on screen.
 */

import { DndContext, PointerSensor, closestCenter, useSensor, useSensors } from "@dnd-kit/core";
import type { DragEndEvent } from "@dnd-kit/core";
import { useMemo, useState } from "react";
import { Column } from "./components/Column";
import { DependencyGraph } from "./components/DependencyGraph";
import { SuggestionPanel } from "./components/SuggestionPanel";
import { TaskDetail } from "./components/TaskDetail";
import { COLUMNS } from "./types";
import type { Task, TaskStatus } from "./types";
import { useBoard } from "./useBoard";

export default function App() {
  const board = useBoard();
  const [openTaskId, setOpenTaskId] = useState<string | null>(null);
  const [showGraph, setShowGraph] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState("");

  // A small drag threshold so clicking a card opens it instead of starting a
  // drag. Without this, every click would be interpreted as a 0px drag.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );

  /** taskId -> titles of the prerequisites that are not yet Done. */
  const blockedBy = useMemo(() => {
    const byId = new Map(board.tasks.map((t) => [t.id, t]));
    const map = new Map<string, string[]>();

    for (const dependency of board.dependencies) {
      const upstream = byId.get(dependency.upstream_id);
      if (!upstream || upstream.status === "DONE") continue;

      const existing = map.get(dependency.downstream_id) ?? [];
      existing.push(upstream.title);
      map.set(dependency.downstream_id, existing);
    }
    return map;
  }, [board.tasks, board.dependencies]);

  const tasksByColumn = useMemo(() => {
    const grouped = new Map<TaskStatus, Task[]>(COLUMNS.map((c) => [c.id, []]));
    for (const task of board.tasks) {
      grouped.get(task.status)?.push(task);
    }
    for (const list of grouped.values()) {
      list.sort((a, b) => a.position - b.position);
    }
    return grouped;
  }, [board.tasks]);

  const openTask = openTaskId
    ? board.tasks.find((t) => t.id === openTaskId) ?? null
    : null;

  /**
   * Drag-and-drop, for both cases:
   *
   *   dropped on a COLUMN  -> append to the end of that column
   *   dropped on a CARD    -> insert immediately before that card
   *
   * Either way exactly one row is written, because `position` is a float: a
   * card landing between 1.0 and 2.0 simply takes 1.5. No renumbering.
   */
  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over) return;

    const task = board.tasks.find((t) => t.id === active.id);
    if (!task || active.id === over.id) return;

    const overId = String(over.id);
    const overColumn = COLUMNS.find((c) => c.id === overId);

    if (overColumn) {
      // Dropped on empty column space. Moving within the same column this way
      // is a no-op: there is no card to position relative to.
      if (task.status === overColumn.id) return;

      const columnTasks = tasksByColumn.get(overColumn.id) ?? [];
      const lastPosition = columnTasks.length
        ? Math.max(...columnTasks.map((t) => t.position))
        : 0;

      void board.moveTask(task, overColumn.id, lastPosition + 1);
      return;
    }

    // Dropped on another card: insert directly above it.
    const target = board.tasks.find((t) => t.id === overId);
    if (!target) return;

    const columnTasks = (tasksByColumn.get(target.status) ?? []).filter(
      (t) => t.id !== task.id,
    );
    const index = columnTasks.findIndex((t) => t.id === target.id);
    const above = index > 0 ? columnTasks[index - 1] : null;

    // Midpoint between the card above and the target, or one step before the
    // target when it is already first in the column.
    const position = above
      ? (above.position + target.position) / 2
      : target.position - 1;

    void board.moveTask(task, target.status, position);
  }

  async function handleCreate() {
    if (!newTitle.trim()) return;
    const today = new Date().toISOString().slice(0, 10);
    const ok = await board.createTask({
      title: newTitle.trim(),
      start_date: today,
      duration_days: 1,
    });
    if (ok) {
      setNewTitle("");
      setCreating(false);
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <h1 className="topbar__title">TaskFlow Pro</h1>
          <p className="topbar__subtitle">
            Dependency-aware Kanban. The engine decides what is ready and how
            changes cascade.
          </p>
        </div>

        <div className="topbar__actions">
          {creating ? (
            <div className="topbar__create">
              <input
                autoFocus
                placeholder="New task title"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void handleCreate();
                  if (e.key === "Escape") setCreating(false);
                }}
              />
              <button className="button button--primary" onClick={handleCreate} type="button">
                Add
              </button>
              <button className="button" onClick={() => setCreating(false)} type="button">
                Cancel
              </button>
            </div>
          ) : (
            <button
              className="button button--primary"
              onClick={() => setCreating(true)}
              type="button"
            >
              + New task
            </button>
          )}
          <button
            className={`button ${showGraph ? "button--primary" : ""}`}
            onClick={() => setShowGraph((v) => !v)}
            type="button"
          >
            {showGraph ? "Hide graph" : "Dependency graph"}
          </button>
          <SuggestionPanel tasks={board.tasks} onAccepted={() => void board.reload()} />
          <button className="button" onClick={() => void board.reload()} type="button">
            Refresh
          </button>
        </div>
      </header>

      {board.notice && (
        <div className={`notice notice--${board.notice.kind}`} role="alert">
          <div>
            <strong>{board.notice.message}</strong>
            {board.notice.path && board.notice.path.length > 0 && (
              <p className="notice__path">{board.notice.path.join("  →  ")}</p>
            )}
          </div>
          <button
            className="notice__close"
            onClick={() => board.setNotice(null)}
            type="button"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      )}

      {!board.loading && showGraph && (
        <DependencyGraph
          tasks={board.tasks}
          dependencies={board.dependencies}
          criticalPath={board.criticalPath}
          selectedId={openTaskId}
          onSelect={setOpenTaskId}
          onClose={() => setShowGraph(false)}
        />
      )}

      {board.loading ? (
        <p className="loading">Loading board…</p>
      ) : (
        <main className="layout">
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <div className="board">
              {COLUMNS.map((column) => (
                <Column
                  key={column.id}
                  id={column.id}
                  label={column.label}
                  tasks={tasksByColumn.get(column.id) ?? []}
                  criticalPath={board.criticalPath}
                  overConstrained={board.overConstrained}
                  blockedBy={blockedBy}
                  onOpen={(task) => setOpenTaskId(task.id)}
                />
              ))}
            </div>
          </DndContext>

          {openTask && (
            <TaskDetail
              task={openTask}
              allTasks={board.tasks}
              dependencies={board.dependencies}
              onClose={() => setOpenTaskId(null)}
              onSave={(changes) => board.updateTask(openTask, changes)}
              onDelete={async () => {
                const ok = await board.deleteTask(openTask);
                if (ok) setOpenTaskId(null);
                return ok;
              }}
              onAddDependency={(upstreamId) =>
                board.addDependency(upstreamId, openTask.id)
              }
              onRemoveDependency={board.removeDependency}
            />
          )}
        </main>
      )}
    </div>
  );
}
