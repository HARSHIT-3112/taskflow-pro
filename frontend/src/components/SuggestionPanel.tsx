/**
 * The human-in-the-loop review drawer for AI dependency suggestions.
 *
 * Nothing here writes to the graph. Accepting calls the server's accept
 * endpoint, which routes through the SAME validated path a hand-drawn edge
 * uses - so an accepted suggestion still has to pass the engine's cycle check.
 *
 * The rationale is shown prominently and the confidence quietly, on purpose:
 * a reviewer should judge the reasoning, not a number.
 */

import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { Suggestion, Task } from "../types";

interface Props {
  tasks: Task[];
  /** Called after an accept, so the board can merge the resulting cascade. */
  onAccepted: () => void;
}

export function SuggestionPanel({ tasks, onAccepted }: Props) {
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [aiEnabled, setAiEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  const byId = new Map(tasks.map((task) => [task.id, task]));

  const refresh = useCallback(async () => {
    try {
      const result = await api.getSuggestions();
      setSuggestions(result.suggestions);
      setAiEnabled(result.ai_enabled);
    } catch {
      setAiEnabled(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.generateSuggestions();
      setSuggestions(result.suggestions);
      // An empty list with no error genuinely means "nothing to suggest",
      // which the prompt explicitly tells the model is a correct answer.
      setError(
        result.error ??
          (result.suggestions.length === 0
            ? "No new dependencies were confidently identified."
            : null),
      );
    } catch (err) {
      setError(err instanceof api.ApiError ? err.message : "Request failed.");
    } finally {
      setBusy(false);
    }
  }

  async function accept(id: string) {
    setBusy(true);
    setError(null);
    try {
      await api.acceptSuggestion(id);
      await refresh();
      onAccepted();
    } catch (err) {
      // The board may have changed since the suggestion was made, so the edge
      // can be refused here exactly as a manual one would be.
      setError(err instanceof api.ApiError ? err.message : "Could not accept.");
    } finally {
      setBusy(false);
    }
  }

  async function reject(id: string) {
    setBusy(true);
    try {
      const result = await api.rejectSuggestion(id);
      setSuggestions(result.suggestions);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button className="button suggest__toggle" onClick={() => setOpen(true)} type="button">
        AI suggestions
        {suggestions.length > 0 && <span className="suggest__count">{suggestions.length}</span>}
      </button>
    );
  }

  return (
    <div className="suggest">
      <header className="suggest__head">
        <div>
          <h3 className="suggest__title">AI dependency suggestions</h3>
          <p className="suggest__note">
            Proposed by a model, applied only when you accept. Every accepted edge
            still passes the engine's cycle check.
          </p>
        </div>
        <button className="detail__close" onClick={() => setOpen(false)} type="button">
          ×
        </button>
      </header>

      {!aiEnabled && (
        <p className="suggest__disabled">
          Not configured. Set <code>ANTHROPIC_API_KEY</code> in <code>.env</code> and
          restart the API. The rest of the board works without it.
        </p>
      )}

      {aiEnabled && (
        <button
          className="button button--primary"
          onClick={generate}
          disabled={busy}
          type="button"
        >
          {busy ? "Analysing…" : "Analyse board"}
        </button>
      )}

      {error && <p className="suggest__error">{error}</p>}

      <ul className="suggest__list">
        {suggestions.map((suggestion) => {
          const upstream = byId.get(suggestion.upstream_id);
          const downstream = byId.get(suggestion.downstream_id);

          return (
            <li key={suggestion.id} className="suggest__item">
              <p className="suggest__edge">
                <strong>{upstream?.title ?? "?"}</strong>
                <span className="suggest__arrow"> must finish before </span>
                <strong>{downstream?.title ?? "?"}</strong>
              </p>
              <p className="suggest__why">{suggestion.rationale}</p>
              <div className="suggest__actions">
                <span className="suggest__confidence">
                  confidence {(suggestion.confidence * 100).toFixed(0)}%
                </span>
                <button
                  className="button button--primary"
                  onClick={() => accept(suggestion.id)}
                  disabled={busy}
                  type="button"
                >
                  Accept
                </button>
                <button
                  className="button"
                  onClick={() => reject(suggestion.id)}
                  disabled={busy}
                  type="button"
                >
                  Reject
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
