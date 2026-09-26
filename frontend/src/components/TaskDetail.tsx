/**
 * Side panel for one task: edit its schedule and manage its prerequisites.
 *
 * Editing duration here is what demonstrates the no-compounding rule - the
 * cascade repaints across the whole board when you save.
 */

import { useEffect, useState } from "react";
import type { Dependency, Task } from "../types";

interface Props {
  task: Task;
  allTasks: Task[];
  dependencies: Dependency[];
  onClose: () => void;
  onSave: (changes: { title?: string; description?: string; start_date?: string; duration_days?: number; pinned?: boolean }) => Promise<boolean>;
  onDelete: () => Promise<boolean>;
  onAddDependency: (upstreamId: string) => Promise<boolean>;
  onRemoveDependency: (dependencyId: string) => Promise<boolean>;
}

export function TaskDetail({
  task,
  allTasks,
  dependencies,
  onClose,
  onSave,
  onDelete,
  onAddDependency,
  onRemoveDependency,
}: Props) {
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description);
  const [startDate, setStartDate] = useState(task.start_date);
  const [duration, setDuration] = useState(task.duration_days);
  const [pinned, setPinned] = useState(task.pinned);
  const [newUpstream, setNewUpstream] = useState("");
  const [saving, setSaving] = useState(false);

  // The server may move this task while the panel is open (a cascade from an
  // edit elsewhere). Re-sync the form when that happens.
  useEffect(() => {
    setTitle(task.title);
    setDescription(task.description);
    setStartDate(task.start_date);
    setDuration(task.duration_days);
    setPinned(task.pinned);
  }, [task]);

  const byId = new Map(allTasks.map((t) => [t.id, t]));

  // Edges where this task waits on something else.
  const prerequisites = dependencies.filter((d) => d.downstream_id === task.id);
  // Edges where something else waits on this task.
  const dependents = dependencies.filter((d) => d.upstream_id === task.id);

  // Anything already linked, or the task itself, cannot be added again.
  const linkedIds = new Set([
    task.id,
    ...prerequisites.map((d) => d.upstream_id),
    ...dependents.map((d) => d.downstream_id),
  ]);
  const candidates = allTasks.filter((t) => !linkedIds.has(t.id));

  const dirty =
    title !== task.title ||
    description !== task.description ||
    startDate !== task.start_date ||
    duration !== task.duration_days ||
    pinned !== task.pinned;

  async function handleSave() {
    setSaving(true);
    await onSave({
      title,
      description,
      start_date: startDate,
      duration_days: duration,
      pinned,
    });
    setSaving(false);
  }

  const bindingConstraint = task.binding_constraint_id
    ? byId.get(task.binding_constraint_id)
    : null;

  return (
    <aside className="detail">
      <header className="detail__head">
        <h2>Task</h2>
        <button className="detail__close" onClick={onClose} type="button" aria-label="Close">
          ×
        </button>
      </header>

      <div className="detail__body">
        <label className="field">
          <span className="field__label">Title</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>

        <label className="field">
          <span className="field__label">Description</span>
          <textarea
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>

        <div className="field-row">
          <label className="field">
            <span className="field__label">Start date</span>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </label>

          <label className="field">
            <span className="field__label">Duration (days)</span>
            <input
              type="number"
              min={1}
              max={365}
              value={duration}
              onChange={(e) => setDuration(Number(e.target.value))}
            />
          </label>
        </div>

        <p className="field__hint">
          End date is <strong>{task.end_date}</strong>, derived from duration. Changing
          either value cascades to every downstream task.
        </p>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={pinned}
            onChange={(e) => setPinned(e.target.checked)}
          />
          <span>Pin this date (the engine will not move it)</span>
        </label>

        {bindingConstraint && (
          <p className="detail__why">
            Starts when it does because <strong>{bindingConstraint.title}</strong> finishes
            on {bindingConstraint.end_date}.
          </p>
        )}

        <div className="detail__actions">
          <button
            className="button button--primary"
            onClick={handleSave}
            disabled={!dirty || saving}
            type="button"
          >
            {saving ? "Saving…" : "Save changes"}
          </button>
          <button className="button button--danger" onClick={onDelete} type="button">
            Delete
          </button>
        </div>

        <hr className="detail__rule" />

        <section>
          <h3 className="detail__section">Prerequisites</h3>
          <p className="field__hint">
            This task cannot start until these finish. It is Ready only once all of
            them are Done.
          </p>

          {prerequisites.length === 0 && <p className="detail__none">None</p>}

          <ul className="linklist">
            {prerequisites.map((dependency) => {
              const upstream = byId.get(dependency.upstream_id);
              return (
                <li key={dependency.id} className="linklist__item">
                  <span>
                    {upstream?.title ?? dependency.upstream_id}
                    {dependency.origin === "AI_ACCEPTED" && (
                      <em className="linklist__origin" title="Suggested by AI, accepted by a person">
                        AI
                      </em>
                    )}
                  </span>
                  <button
                    className="linklist__remove"
                    onClick={() => onRemoveDependency(dependency.id)}
                    type="button"
                    aria-label="Remove prerequisite"
                  >
                    ×
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="detail__add">
            <select value={newUpstream} onChange={(e) => setNewUpstream(e.target.value)}>
              <option value="">Add a prerequisite…</option>
              {candidates.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {candidate.title}
                </option>
              ))}
            </select>
            <button
              className="button"
              disabled={!newUpstream}
              onClick={async () => {
                const ok = await onAddDependency(newUpstream);
                if (ok) setNewUpstream("");
              }}
              type="button"
            >
              Add
            </button>
          </div>
        </section>

        {dependents.length > 0 && (
          <section>
            <h3 className="detail__section">Blocks</h3>
            <p className="field__hint">These tasks wait on this one.</p>
            <ul className="linklist">
              {dependents.map((dependency) => (
                <li key={dependency.id} className="linklist__item">
                  <span>{byId.get(dependency.downstream_id)?.title}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </aside>
  );
}
