/**
 * Visual view of the dependency DAG.
 *
 * The board shows prerequisites as text ("Waiting on: …"), which makes a
 * diamond or a deep chain something the reader has to reconstruct mentally.
 * This draws the actual graph.
 *
 * Layout: nodes are placed in columns by TOPOLOGICAL DEPTH — the same ordering
 * concept the engine uses to schedule. A task's depth is one more than the
 * deepest of its prerequisites, so every edge points strictly left-to-right and
 * the drawing can never have a backwards arrow. That is a visual consequence of
 * the graph being acyclic, which is the property the engine enforces.
 */

import { useMemo } from "react";
import type { Dependency, Task } from "../types";

interface Props {
  tasks: Task[];
  dependencies: Dependency[];
  criticalPath: string[];
  selectedId: string | null;
  onSelect: (taskId: string) => void;
  onClose: () => void;
}

const NODE_WIDTH = 168;
const NODE_HEIGHT = 52;
const COLUMN_GAP = 76;
const ROW_GAP = 16;
const PADDING = 22;

interface Placed {
  task: Task;
  x: number;
  y: number;
}

/**
 * Longest-path depth for every task.
 *
 * depth(t) = 0 when t has no prerequisites, else 1 + max(depth of each).
 * Computed by repeated relaxation, which terminates because the graph is a DAG
 * — the same guarantee that makes the engine's topological sort possible.
 */
function computeDepths(tasks: Task[], dependencies: Dependency[]): Map<string, number> {
  const ids = new Set(tasks.map((t) => t.id));
  const incoming = new Map<string, string[]>();

  for (const task of tasks) incoming.set(task.id, []);
  for (const dependency of dependencies) {
    // Ignore edges pointing at tasks that no longer exist.
    if (!ids.has(dependency.upstream_id) || !ids.has(dependency.downstream_id)) continue;
    incoming.get(dependency.downstream_id)!.push(dependency.upstream_id);
  }

  const depth = new Map<string, number>(tasks.map((t) => [t.id, 0]));

  // At most `tasks.length` passes are needed for depths to stop changing.
  for (let pass = 0; pass < tasks.length; pass++) {
    let changed = false;
    for (const task of tasks) {
      const prerequisites = incoming.get(task.id) ?? [];
      if (prerequisites.length === 0) continue;

      const deepest = Math.max(...prerequisites.map((id) => depth.get(id) ?? 0));
      if (deepest + 1 > (depth.get(task.id) ?? 0)) {
        depth.set(task.id, deepest + 1);
        changed = true;
      }
    }
    if (!changed) break;
  }

  return depth;
}

export function DependencyGraph({
  tasks,
  dependencies,
  criticalPath,
  selectedId,
  onSelect,
  onClose,
}: Props) {
  const { placed, width, height, positionById } = useMemo(() => {
    const depths = computeDepths(tasks, dependencies);

    // Bucket tasks into columns by depth.
    const columns = new Map<number, Task[]>();
    for (const task of tasks) {
      const d = depths.get(task.id) ?? 0;
      if (!columns.has(d)) columns.set(d, []);
      columns.get(d)!.push(task);
    }

    const placed: Placed[] = [];
    const positionById = new Map<string, Placed>();

    const sortedDepths = [...columns.keys()].sort((a, b) => a - b);
    for (const d of sortedDepths) {
      const column = columns.get(d)!;
      // Stable order within a column so the picture does not jump around.
      column.sort((a, b) => a.title.localeCompare(b.title));

      column.forEach((task, row) => {
        const entry = {
          task,
          x: PADDING + d * (NODE_WIDTH + COLUMN_GAP),
          y: PADDING + row * (NODE_HEIGHT + ROW_GAP),
        };
        placed.push(entry);
        positionById.set(task.id, entry);
      });
    }

    const tallest = Math.max(1, ...[...columns.values()].map((c) => c.length));
    return {
      placed,
      positionById,
      width: PADDING * 2 + sortedDepths.length * NODE_WIDTH + Math.max(0, sortedDepths.length - 1) * COLUMN_GAP,
      height: PADDING * 2 + tallest * NODE_HEIGHT + Math.max(0, tallest - 1) * ROW_GAP,
    };
  }, [tasks, dependencies]);

  const criticalSet = new Set(criticalPath);

  /** True when this edge joins two consecutive tasks on the critical path. */
  const isCriticalEdge = (upstreamId: string, downstreamId: string) => {
    const i = criticalPath.indexOf(upstreamId);
    return i >= 0 && criticalPath[i + 1] === downstreamId;
  };

  return (
    <section className="graph">
      <header className="graph__head">
        <div>
          <h3 className="graph__title">Dependency graph</h3>
          <p className="graph__note">
            Columns are topological depth. Every arrow points right, because the
            graph is acyclic — the engine will not let it be otherwise.
          </p>
        </div>
        <button className="detail__close" onClick={onClose} type="button" aria-label="Close">
          ×
        </button>
      </header>

      <div className="graph__legend">
        <span><i className="dot dot--ready" /> Ready</span>
        <span><i className="dot dot--blocked" /> Blocked</span>
        <span><i className="dot dot--done" /> Done</span>
        <span><i className="dot dot--critical" /> Critical path</span>
      </div>

      <div className="graph__scroll">
        <svg width={width} height={height} className="graph__svg" role="img"
             aria-label="Dependency graph of all tasks">
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5"
                    markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#9aa4ae" />
            </marker>
            <marker id="arrow-critical" viewBox="0 0 10 10" refX="9" refY="5"
                    markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#7c3aed" />
            </marker>
          </defs>

          {/* Edges first, so nodes paint over them. */}
          {dependencies.map((dependency) => {
            const from = positionById.get(dependency.upstream_id);
            const to = positionById.get(dependency.downstream_id);
            if (!from || !to) return null;

            const x1 = from.x + NODE_WIDTH;
            const y1 = from.y + NODE_HEIGHT / 2;
            const x2 = to.x;
            const y2 = to.y + NODE_HEIGHT / 2;
            // Horizontal control points give a clean S-curve between columns.
            const curve = Math.max(28, (x2 - x1) / 2);

            const critical = isCriticalEdge(dependency.upstream_id, dependency.downstream_id);

            return (
              <path
                key={dependency.id}
                d={`M ${x1} ${y1} C ${x1 + curve} ${y1}, ${x2 - curve} ${y2}, ${x2} ${y2}`}
                className={critical ? "edge edge--critical" : "edge"}
                markerEnd={critical ? "url(#arrow-critical)" : "url(#arrow)"}
              />
            );
          })}

          {placed.map(({ task, x, y }) => {
            const done = task.status === "DONE";
            const blocked = task.dependency_state === "BLOCKED" && !done;
            const critical = criticalSet.has(task.id);
            const selected = task.id === selectedId;

            return (
              <g
                key={task.id}
                transform={`translate(${x}, ${y})`}
                className={[
                  "node",
                  done ? "node--done" : blocked ? "node--blocked" : "node--ready",
                  critical ? "node--critical" : "",
                  selected ? "node--selected" : "",
                ].filter(Boolean).join(" ")}
                onClick={() => onSelect(task.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") onSelect(task.id);
                }}
              >
                <rect width={NODE_WIDTH} height={NODE_HEIGHT} rx={7} className="node__box" />
                <text x={10} y={20} className="node__title">
                  {task.title.length > 24 ? `${task.title.slice(0, 23)}…` : task.title}
                </text>
                <text x={10} y={37} className="node__meta">
                  {task.start_date.slice(5)} → {task.end_date.slice(5)} · {task.duration_days}d
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </section>
  );
}
