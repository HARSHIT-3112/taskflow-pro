"""Model provider adapters for the dependency-suggestion feature.

The suggestion service needs one thing from a model: given a prompt and a
Pydantic schema, return instances of that schema or an error string. That is
the entire contract, and it is deliberately narrow.

Keeping it narrow means the safety properties that actually matter -
closed-world prompting, schema enforcement, id allowlisting, the engine's cycle
check, the confidence floor, human acceptance - live in suggestions.py and are
completely provider-independent. Swapping model vendors changes this file and
nothing else.

Two providers are supported. Whichever key is configured is used; if both are,
Gemini is preferred because its free tier makes the feature reproducible for
anyone running this project.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash"
ANTHROPIC_MODEL = "claude-opus-5"


class LLMResult[M: BaseModel]:
    """Either a parsed response or a human-readable error, never both.

    Returning the error rather than raising is deliberate: a suggestion is an
    assistant feature and must never take the board down. But the error is
    carried back so the UI can say what went wrong - "the call failed" and "the
    model found nothing" must not look the same to a user.
    """

    def __init__(self, value: M | None = None, error: str | None = None) -> None:
        self.value = value
        self.error = error


def active_provider() -> str | None:
    """Which provider will be used, or None when nothing is configured."""
    if settings.gemini_api_key.strip():
        return "gemini"
    if settings.anthropic_api_key.strip():
        return "anthropic"
    return None


def generate_structured[T: BaseModel](
    *,
    system_prompt: str,
    user_prompt: str,
    schema: type[T],
) -> LLMResult[T]:
    """Ask the configured model for a response matching `schema`."""
    provider = active_provider()

    if provider == "gemini":
        return _gemini(system_prompt, user_prompt, schema)
    if provider == "anthropic":
        return _anthropic(system_prompt, user_prompt, schema)

    return LLMResult(error="No model provider is configured.")


def _gemini[T: BaseModel](system_prompt: str, user_prompt: str, schema: type[T]) -> LLMResult[T]:
    """Google Gemini via the google-genai SDK.

    `response_schema` makes the model's output conform to the Pydantic model,
    so malformed JSON is not a case we have to handle downstream.
    """
    from google import genai
    from google.genai import errors as genai_errors

    client = genai.Client(api_key=settings.gemini_api_key)

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_prompt,
            config={
                "system_instruction": system_prompt,
                "response_mime_type": "application/json",
                "response_schema": schema,
                # Low temperature: near-deterministic suggestions for the same
                # board, so the feature behaves predictably in a demo.
                "temperature": 0.2,
            },
        )
    except genai_errors.ClientError as exc:
        logger.exception("Gemini rejected the request")
        message = str(exc)
        if "API_KEY_INVALID" in message or "API key not valid" in message:
            return LLMResult(error="The configured GEMINI_API_KEY was rejected.")
        if "RESOURCE_EXHAUSTED" in message or "429" in message:
            return LLMResult(
                error="Gemini rate limit reached. Wait a moment and try again."
            )
        return LLMResult(error="Gemini rejected the request. See the server log.")
    except Exception:
        logger.exception("Gemini request failed")
        return LLMResult(error="Could not reach Gemini. See the server log.")

    parsed = response.parsed
    if not isinstance(parsed, schema):
        logger.warning("Gemini returned no parseable response")
        return LLMResult(error="The model returned a response that did not match the schema.")

    return LLMResult(value=parsed)


def _anthropic[T: BaseModel](system_prompt: str, user_prompt: str, schema: type[T]) -> LLMResult[T]:
    """Anthropic Claude via the official SDK.

    Note: Claude Opus 5 removed the sampling parameters, so there is no
    temperature to set here. Determinism comes from the structured output and
    the confidence floor applied by the caller.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    try:
        response = client.messages.parse(
            model=ANTHROPIC_MODEL,
            max_tokens=4000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            output_format=schema,
        )
    except anthropic.AuthenticationError:
        logger.exception("Anthropic authentication failed")
        return LLMResult(error="The configured ANTHROPIC_API_KEY was rejected.")
    except anthropic.RateLimitError:
        logger.exception("Anthropic rate limit hit")
        return LLMResult(error="Rate limited by the Anthropic API. Try again shortly.")
    except anthropic.BadRequestError as exc:
        logger.exception("Anthropic rejected the request")
        if "credit balance is too low" in str(exc):
            return LLMResult(
                error=(
                    "The Anthropic account has no credits. Add credits, or set "
                    "GEMINI_API_KEY instead."
                )
            )
        return LLMResult(error="The Anthropic API rejected the request. See the server log.")
    except Exception:
        logger.exception("Anthropic request failed")
        return LLMResult(error="Could not reach the Anthropic API. See the server log.")

    parsed = response.parsed_output
    if parsed is None:
        logger.warning("Anthropic returned no parseable response")
        return LLMResult(error="The model returned a response that did not match the schema.")

    return LLMResult(value=parsed)
