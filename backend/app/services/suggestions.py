"""AI-assisted dependency suggestion.

The model PROPOSES edges. It never writes to the graph. Acceptance goes through
the same validated endpoint a hand-drawn edge uses, so the engine's cycle check
is unavoidable even if a reviewer clicks accept carelessly.

Grounding is layered so that no single failure is enough to corrupt the graph:

  1. Closed-world prompt   the model may reference only ids we sent it
  2. Schema-enforced output structured outputs, parsed into a Pydantic model
  3. Allowlist validation  unknown ids, self-edges, duplicates and previously
                           rejected pairs are discarded before anything is shown
  4. Engine as final gate  every surviving candidate is run through cycle
                           detection before it is even offered
  5. Calibration           a confidence floor, with the rationale shown so the
                           reviewer judges the reasoning rather than the number
  6. Provenance            accepted edges are stored as AI_ACCEPTED

Provider choice lives in app/services/llm.py. Gemini and Claude are both
supported and the validation below is identical for either, because the
grounding layers do not depend on which model answered.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.config import settings
from app.domain import SuggestionStatus
from app.engine.graph import find_cycle_path
from app.models import Dependency, Suggestion, Task
from app.services.llm import generate_structured
from app.services.scheduler import load_graph, to_engine_types

logger = logging.getLogger(__name__)

# Below this, a suggestion is noise and is not worth a reviewer's attention.
CONFIDENCE_FLOOR = 0.55

# One call covers the whole board, so cost stays flat as tasks are added.
MAX_SUGGESTIONS = 12

SYSTEM_PROMPT = """You analyse software project tasks and identify which ones \
are genuine prerequisites of others.

A dependency U -> V means: work on V cannot sensibly begin until U is finished.

Rules you must follow:

1. You may ONLY reference task ids that appear in the list given to you. \
Inventing an id is a failure.
2. Propose a dependency only when the task text gives you concrete evidence. \
Your rationale must quote the words that justify it.
3. Returning an empty list is a CORRECT answer when nothing is clearly \
dependent. Do not pad the list to seem useful.
4. Do not propose a dependency that already exists in the list of current \
dependencies you are given.
5. Prefer a few high-confidence relationships over many speculative ones.
6. Set confidence honestly: 0.9+ only when the text states the dependency \
almost explicitly, 0.6-0.8 when it is a strong inference, below 0.6 when you \
are guessing.

Think about the real order of software work: schemas before the APIs that use \
them, APIs before the clients that call them, implementation before the tests \
that exercise it, deployment before anything that measures the deployed system."""


class ProposedEdge(BaseModel):
    """One suggestion from the model. Field docs are sent as schema hints."""

    upstream_id: str = Field(description="Id of the task that must finish first")
    downstream_id: str = Field(description="Id of the task that waits")
    confidence: float = Field(ge=0.0, le=1.0, description="0..1, honestly calibrated")
    rationale: str = Field(
        description="One sentence quoting the evidence from the task text",
        max_length=300,
    )


class ProposedEdges(BaseModel):
    suggestions: list[ProposedEdge]


def _build_prompt(tasks: list[Task], dependencies: list[Dependency]) -> str:
    """Describe the board as plain text.

    Only id, title and description are sent. Dates, statuses and board positions
    are irrelevant to whether one task is a prerequisite of another, and sending
    less means less for the model to get distracted by.
    """
    lines = ["Tasks on this board:", ""]
    for task in tasks:
        lines.append(f"- id: {task.id}")
        lines.append(f"  title: {task.title}")
        if task.description:
            lines.append(f"  description: {task.description}")
    lines.append("")

    if dependencies:
        by_id = {t.id: t.title for t in tasks}
        lines.append("Dependencies that ALREADY exist (do not propose these again):")
        for dependency in dependencies:
            upstream = by_id.get(dependency.upstream_id, dependency.upstream_id)
            downstream = by_id.get(dependency.downstream_id, dependency.downstream_id)
            lines.append(f"- {upstream} -> {downstream}")
    else:
        lines.append("No dependencies exist yet.")

    lines.append("")
    lines.append(
        "Identify prerequisite relationships that are missing. "
        "Return an empty list if none are clearly justified."
    )
    return "\n".join(lines)


def _ask_model(prompt: str) -> tuple[list[ProposedEdge], str | None]:
    """Ask the configured model for candidate edges.

    Which provider answers is decided in app/services/llm.py. Everything below
    this line in the file - the validation layers - is identical either way,
    which is the point: the safety properties are structural, not a property of
    any particular vendor.
    """
    result = generate_structured(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=prompt,
        schema=ProposedEdges,
    )
    if result.error is not None or result.value is None:
        return [], result.error or "The model returned nothing usable."
    return result.value.suggestions, None


def generate_suggestions(session: Session) -> tuple[list[Suggestion], str | None]:
    """Generate, validate and store dependency suggestions.

    Everything the model returns is filtered before it reaches a human. The
    order of the checks below is deliberate: cheap structural checks first, the
    engine's cycle check last, because it is the only one that has to walk the
    graph.
    """
    if not settings.ai_enabled:
        return [], "AI suggestions are not configured."

    tasks, dependencies = load_graph(session)
    if len(tasks) < 2:
        return [], None

    proposals, error = _ask_model(_build_prompt(tasks, dependencies))
    if error is not None:
        return [], error
    if not proposals:
        return [], None

    # --- Layer 3: allowlist validation -------------------------------------
    valid_ids = {task.id for task in tasks}
    existing_edges = {(d.upstream_id, d.downstream_id) for d in dependencies}

    # Pairs a human already rejected must never be proposed again.
    previously_seen = {
        (s.upstream_id, s.downstream_id)
        for s in session.exec(select(Suggestion)).all()
    }

    nodes, edges = to_engine_types(tasks, dependencies)
    node_ids = [n.id for n in nodes]

    accepted: list[Suggestion] = []
    seen_in_batch: set[tuple[str, str]] = set()

    for proposal in proposals:
        pair = (proposal.upstream_id, proposal.downstream_id)

        if proposal.upstream_id not in valid_ids or proposal.downstream_id not in valid_ids:
            logger.info("Discarded suggestion referencing an unknown task: %s", pair)
            continue
        if proposal.upstream_id == proposal.downstream_id:
            continue
        if pair in existing_edges or pair in previously_seen or pair in seen_in_batch:
            continue
        if proposal.confidence < CONFIDENCE_FLOOR:
            continue

        # --- Layer 4: the engine has the final say -------------------------
        # A suggestion that would close a loop is never shown at all, so a
        # reviewer cannot accept one by accident.
        if find_cycle_path(node_ids, edges, *pair) is not None:
            logger.info("Discarded suggestion that would create a cycle: %s", pair)
            continue

        seen_in_batch.add(pair)
        accepted.append(
            Suggestion(
                upstream_id=proposal.upstream_id,
                downstream_id=proposal.downstream_id,
                confidence=proposal.confidence,
                rationale=proposal.rationale,
                status=SuggestionStatus.PENDING,
            )
        )

        if len(accepted) >= MAX_SUGGESTIONS:
            break

    for suggestion in accepted:
        session.add(suggestion)
    session.flush()

    logger.info(
        "Model proposed %d edges; %d survived validation", len(proposals), len(accepted)
    )
    return accepted, None
