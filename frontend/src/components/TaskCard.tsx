/**
 * One task card.
 *
 * Presentational: it renders what it is given and reports clicks upward. It
 * holds no state and never calls the API.
 */

import { useDraggable, useDroppable } from "@dnd-kit/core";
import type { Task } from "../types";

interface Props {
  task: Task;
  /** Titles of this task's prerequisites, for the "waiting on" line. */
  blockedBy: string[];
  /** True when the task sits on the longest dependency chain. */
  onCriticalPath: boolean;
  /** True when a pin conflicts with what the prerequisites require. */
  overConstrained: boolean;
  onOpen: (task: Task) => void;
}

/** "2026-09-21" -> "21 Sep" */
function formatDate(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export function TaskCard({
  task,
  blockedBy,
  onCriticalPath,
  overConstrained,
  onOpen,
}: Props) {
  // A card is both draggable and a drop target. Being droppable is what makes
  // reordering possible: dropping card A onto card B means "put A just before
  // B", which the board turns into a fractional position between B and its
  // neighbour.
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: task.id,
  });
  const { setNodeRef: setDropRef, isOver } = useDroppable({ id: task.id });

  /** Attach both dnd-kit refs to the same element. */
  const setRefs = (node: HTMLElement | null) => {
    setNodeRef(node);
    setDropRef(node);
  };

  const style = transform
    ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` }
    : undefined;

  const isBlocked = task.dependency_state === "BLOCKED";
  // A finished task's blocked/ready badge is meaningless noise, so hide it.
  const showState = task.status !== "DONE";

  return (
    <article
      ref={setRefs}
      style={style}
      className={[
        "card",
        isDragging ? "card--dragging" : "",
        isOver && !isDragging ? "card--drop-before" : "",
        onCriticalPath ? "card--critical" : "",
        isBlocked && showState ? "card--blocked" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      {...listeners}
      {...attributes}
    >
      <button className="card__open" onClick={() => onOpen(task)} type="button">
        <header className="card__head">
          <h3 className="card__title">{task.title}</h3>
          {task.pinned && <span className="card__pin" title="Pinned: the engine will not move this task">📌</span>}
        </header>

        <div className="card__dates">
          {formatDate(task.start_date)} – {formatDate(task.end_date)}
          <span className="card__duration">{task.duration_days}d</span>
        </div>

        <footer className="card__badges">
          {showState && (
            <span className={`badge ${isBlocked ? "badge--blocked" : "badge--ready"}`}>
              {isBlocked ? "Blocked" : "Ready"}
            </span>
          )}
          {onCriticalPath && (
            <span className="badge badge--critical" title="On the longest dependency chain: any delay here delays the project">
              Critical
            </span>
          )}
          {overConstrained && (
            <span className="badge badge--warning" title="Pinned, but its prerequisites require a later start">
              Conflict
            </span>
          )}
        </footer>

        {isBlocked && showState && blockedBy.length > 0 && (
          <p className="card__waiting">Waiting on: {blockedBy.join(", ")}</p>
        )}
      </button>
    </article>
  );
}
