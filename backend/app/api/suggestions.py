"""AI suggestion endpoints.

Note what is absent: there is no endpoint that writes a dependency from a
suggestion directly. Accepting one calls the same `create_edge` used by manual
creation, so an AI-originated edge passes exactly the same validation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, select

from app.api.dependencies import _result
from app.config import settings
from app.db import get_session
from app.domain import DependencyOrigin, SuggestionStatus
from app.models import Suggestion
from app.schemas import MutationResult
from app.services.suggestions import generate_suggestions

router = APIRouter(prefix="/api/suggestions", tags=["suggestions"])


class SuggestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    upstream_id: str
    downstream_id: str
    confidence: float
    rationale: str
    status: SuggestionStatus


class SuggestionList(BaseModel):
    """Pending suggestions, plus whether the feature is configured at all.

    `error` distinguishes "the call failed" from "the model found nothing" -
    an empty list means different things in those two cases.
    """

    ai_enabled: bool
    suggestions: list[SuggestionRead]
    error: str | None = None


def _pending(session: Session) -> list[SuggestionRead]:
    rows = session.exec(
        select(Suggestion).where(Suggestion.status == SuggestionStatus.PENDING)
    ).all()
    return [SuggestionRead.model_validate(row) for row in rows]


@router.get("", response_model=SuggestionList)
def list_suggestions(session: Session = Depends(get_session)) -> SuggestionList:
    return SuggestionList(ai_enabled=settings.ai_enabled, suggestions=_pending(session))


@router.post("/generate", response_model=SuggestionList)
def generate(session: Session = Depends(get_session)) -> SuggestionList:
    """Ask the model for dependency suggestions.

    Returns 503 rather than failing quietly when no API key is configured, so
    the UI can explain why the feature is unavailable instead of appearing broken.
    """
    if not settings.ai_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "AI suggestions are not configured. Set ANTHROPIC_API_KEY in .env "
                "and restart the API. Everything else works without it."
            ),
        )

    _, error = generate_suggestions(session)
    session.commit()
    return SuggestionList(
        ai_enabled=True, suggestions=_pending(session), error=error
    )


@router.post("/{suggestion_id}/accept", response_model=MutationResult)
def accept(
    suggestion_id: str,
    session: Session = Depends(get_session),
) -> MutationResult:
    """A human accepts a suggestion.

    This is the only path from a suggestion into the graph, and it goes through
    the same validated `create_edge` as manual creation. If the board changed
    since the suggestion was made and the edge would now close a loop, this
    raises 409 exactly as a manual attempt would.
    """
    # Imported here rather than at module scope to keep the import graph
    # acyclic: app.api.dependencies already imports from app.schemas.
    from app.api.dependencies import create_edge

    suggestion = session.get(Suggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    if suggestion.status is not SuggestionStatus.PENDING:
        raise HTTPException(status_code=409, detail="This suggestion was already reviewed")

    create_edge(
        session,
        suggestion.upstream_id,
        suggestion.downstream_id,
        0,
        DependencyOrigin.AI_ACCEPTED,
    )

    suggestion.status = SuggestionStatus.ACCEPTED
    session.add(suggestion)

    return _result(session)


@router.post("/{suggestion_id}/reject", response_model=SuggestionList)
def reject(
    suggestion_id: str,
    session: Session = Depends(get_session),
) -> SuggestionList:
    """Reject a suggestion.

    The row is kept rather than deleted so the same pair is never proposed
    again, and so the accept rate stays measurable.
    """
    suggestion = session.get(Suggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    suggestion.status = SuggestionStatus.REJECTED
    session.add(suggestion)
    session.commit()

    return SuggestionList(ai_enabled=settings.ai_enabled, suggestions=_pending(session))
