/**
 * One Kanban column: a drop target plus the cards currently in it.
 */

import { useDroppable } from "@dnd-kit/core";
import type { Task, TaskStatus } from "../types";
import { TaskCard } from "./TaskCard";

interface Props {
  id: TaskStatus;
  label: string;
  tasks: Task[];
  criticalPath: string[];
  overConstrained: string[];
  /** taskId -> titles of its prerequisites, for the "waiting on" line. */
  blockedBy: Map<string, string[]>;
  onOpen: (task: Task) => void;
}

export function Column({
  id,
  label,
  tasks,
  criticalPath,
  overConstrained,
  blockedBy,
  onOpen,
}: Props) {
  // Registers this element as a drop zone. `isOver` is true while a dragged
  // card hovers here, which we use to highlight it.
  const { setNodeRef, isOver } = useDroppable({ id });

  const readyCount = tasks.filter(
    (task) => task.dependency_state === "READY" && task.status !== "DONE",
  ).length;

  return (
    <section
      ref={setNodeRef}
      className={`column ${isOver ? "column--over" : ""}`}
      aria-label={label}
    >
      <header className="column__head">
        <h2 className="column__title">{label}</h2>
        <span className="column__count">{tasks.length}</span>
      </header>

      {readyCount > 0 && id !== "DONE" && (
        <p className="column__ready">{readyCount} ready to start</p>
      )}

      <div className="column__cards">
        {tasks.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            blockedBy={blockedBy.get(task.id) ?? []}
            onCriticalPath={criticalPath.includes(task.id)}
            overConstrained={overConstrained.includes(task.id)}
            onOpen={onOpen}
          />
        ))}

        {tasks.length === 0 && <p className="column__empty">Drop a task here</p>}
      </div>
    </section>
  );
}
