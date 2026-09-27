/**
 * Tests for the card.
 *
 * The card is where the engine's verdict becomes something a person reads, so
 * what matters is that it reports state faithfully - particularly the two
 * places where the display deliberately differs from the raw data.
 */

import { DndContext, PointerSensor, useSensor, useSensors } from "@dnd-kit/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TaskCard } from "./TaskCard";
import type { Task } from "../types";

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "Build REST API endpoints",
    description: "",
    status: "BACKLOG",
    start_date: "2026-09-24",
    end_date: "2026-09-27",
    duration_days: 4,
    pinned: false,
    position: 1,
    dependency_state: "READY",
    version: 0,
    binding_constraint_id: null,
    ...overrides,
  };
}

/**
 * Cards use dnd-kit hooks, so they must render inside a DndContext - and with
 * the SAME sensor configuration the app uses. The 6px activation distance is
 * what lets a click through instead of reading it as a zero-pixel drag, so a
 * test without it would not be testing the real component.
 */
function Harness({ children }: { children: React.ReactNode }) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );
  return <DndContext sensors={sensors}>{children}</DndContext>;
}

function renderCard(props: Partial<Parameters<typeof TaskCard>[0]> = {}) {
  const onOpen = vi.fn();
  render(
    <Harness>
      <TaskCard
        task={makeTask()}
        blockedBy={[]}
        onCriticalPath={false}
        overConstrained={false}
        onOpen={onOpen}
        {...props}
      />
    </Harness>,
  );
  return { onOpen };
}

describe("TaskCard", () => {
  it("shows the title, dates and duration", () => {
    renderCard();
    expect(screen.getByText("Build REST API endpoints")).toBeInTheDocument();
    expect(screen.getByText("4d")).toBeInTheDocument();
    expect(screen.getByText(/24 Sep.*27 Sep/)).toBeInTheDocument();
  });

  it("shows Ready when no prerequisite is outstanding", () => {
    renderCard();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.queryByText("Blocked")).not.toBeInTheDocument();
  });

  it("shows Blocked and names what it is waiting on", () => {
    renderCard({
      task: makeTask({ dependency_state: "BLOCKED" }),
      blockedBy: ["Design database schema", "Build authentication service"],
    });

    expect(screen.getByText("Blocked")).toBeInTheDocument();
    expect(
      screen.getByText(/Design database schema, Build authentication service/),
    ).toBeInTheDocument();
  });

  it("hides the state badge on a Done task", () => {
    // A finished task's blocked/ready badge is meaningless noise, so the card
    // deliberately omits it even though the engine still reports a value.
    renderCard({ task: makeTask({ status: "DONE", dependency_state: "READY" }) });

    expect(screen.queryByText("Ready")).not.toBeInTheDocument();
    expect(screen.queryByText("Blocked")).not.toBeInTheDocument();
  });

  it("shows Blocked on an IN_PROGRESS task", () => {
    // Blocked is orthogonal to the column: the rollback rule requires a task to
    // become blocked while someone is already working on it.
    renderCard({
      task: makeTask({ status: "IN_PROGRESS", dependency_state: "BLOCKED" }),
      blockedBy: ["Design database schema"],
    });

    expect(screen.getByText("Blocked")).toBeInTheDocument();
  });

  it("marks a task on the critical path", () => {
    renderCard({ onCriticalPath: true });
    expect(screen.getByText("Critical")).toBeInTheDocument();
  });

  it("flags a pin that conflicts with its prerequisites", () => {
    renderCard({ task: makeTask({ pinned: true }), overConstrained: true });
    expect(screen.getByText("Conflict")).toBeInTheDocument();
  });

  it("reports a click upward instead of acting on it", async () => {
    const { onOpen } = renderCard();
    await userEvent.click(screen.getByText("Build REST API endpoints"));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
