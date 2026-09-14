"""Typed (Pydantic-validated) generation on top of the structured path.

The caller declares the shape it wants as a ``BaseModel``; this module derives
the JSON Schema, validates the answer, and feeds a validation failure back to
the model as a repair prompt — so no call site ever touches raw JSON text.

Split out of ``structured`` for the module size cap; imported from there under
its historical name.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.services.llm.exceptions import LLMProviderError
from core.services.llm.tool_calling import ResponseFormat

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

logger = get_logger(__name__)

__all__ = ["generate_typed"]


def _extract_json_payload(text: str) -> str:
    """Strip Markdown code fences some models wrap around JSON output."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


async def generate_typed(
    service: LLMService,
    prompt: str,
    response_model: type[Any],
    *,
    model: str | None = None,
    system_prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    task_category: str | None = None,
    retries: int = 1,
    allow_refusal: bool = False,
) -> Any:
    """Generate a validated instance of a Pydantic model (typed output).

    The Pydantic bridge over :func:`generate_structured`: the JSON Schema is
    derived from ``response_model`` (``model_json_schema()``), the response is
    parsed and validated (``model_validate``), and a schema-violating answer
    is retried with the validation error fed back to the model — the caller
    never touches raw JSON text.

    Args:
        service: The owning :class:`LLMService`.
        prompt: User turn.
        response_model: A ``pydantic.BaseModel`` subclass describing the
            expected response shape.
        model / system_prompt / temperature / max_tokens / task_category:
            Same semantics as :func:`generate_structured`.
        retries: Extra attempts on JSON/validation failure (the error text is
            appended to the retry prompt so the model can repair its output).

    Returns:
        A validated ``response_model`` instance.

    Raises:
        LLMProviderError: When no valid instance is produced within
            ``retries + 1`` attempts (carries the last validation error).
    """
    from pydantic import ValidationError

    # Resolved at call time through the module object, so the historical
    # patch target (``structured.generate_structured``) keeps working and
    # the two modules can reference each other.
    from core.services.llm import structured

    response_format = ResponseFormat(
        schema=response_model.model_json_schema(),
        name=response_model.__name__,
    )
    attempt_prompt = prompt
    last_error = ""
    for _attempt in range(max(0, retries) + 1):
        result = await structured.generate_structured(
            service,
            attempt_prompt,
            model=model,
            response_format=response_format,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            task_category=task_category,
            allow_refusal=allow_refusal,
        )
        payload = _extract_json_payload(result.text or "")
        try:
            return response_model.model_validate(json.loads(payload))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)
            logger.warning(
                "generate_typed validation failed (%s), attempt %d",
                type(exc).__name__,
                _attempt + 1,
            )
            attempt_prompt = (
                f"{prompt}\n\nYour previous response was not valid for the "
                f"required schema. Error: {last_error}\n"
                "Respond again with ONLY a JSON object matching the schema."
            )
    raise LLMProviderError(
        f"Typed generation failed validation after {max(0, retries) + 1} "
        f"attempts: {last_error}"
    )
