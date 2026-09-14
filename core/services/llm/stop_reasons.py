"""Stop-reason policy shared by every generation path.

The API tells you *why* a turn ended — and the stack ignored it. Three of the
six reasons change what the answer means:

* ``max_tokens`` — the answer is cut off mid-sentence. Returned as if
  complete, it silently corrupts anything downstream that parses it (JSON,
  a plan, a tool argument);
* ``refusal`` — the model declined. The call succeeded, was billed, and the
  text is empty or an apology; callers that treat it as a normal answer act on
  nothing;
* ``pause_turn`` — a long-running turn was suspended and must be resumed by
  resending the conversation with the assistant content appended. Dropping it
  truncates the work silently.

The first two are decided here, so every path (text, structured, streaming)
applies the same policy. ``pause_turn`` is resumed at the wire level by the
provider, which is the only layer holding the message list; this module just
names the condition and the retry budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.services.llm.errors import LLMRefusalError

if TYPE_CHECKING:
    from core.services.llm.tool_calling import LLMResult

logger = get_logger(__name__)

__all__ = [
    "MAX_PAUSE_TURN_CONTINUATIONS",
    "STOP_MAX_TOKENS",
    "STOP_PAUSE_TURN",
    "STOP_REFUSAL",
    "TRUNCATION_STOP_REASONS",
    "apply_stop_reason",
    "is_paused",
    "raise_for_refusal",
    "stop_details_from",
]

STOP_MAX_TOKENS = "max_tokens"
STOP_PAUSE_TURN = "pause_turn"
STOP_REFUSAL = "refusal"

#: Reasons that mean "the answer is incomplete". ``length`` is OpenAI's
#: spelling of the same condition, so one policy covers both providers.
TRUNCATION_STOP_REASONS = frozenset({STOP_MAX_TOKENS, "length"})

#: How many times a ``pause_turn`` is resumed before giving up. A paused turn
#: that never ends is a runaway cost, so the loop is bounded.
MAX_PAUSE_TURN_CONTINUATIONS = 3


def is_paused(stop_reason: str | None) -> bool:
    """Whether *stop_reason* means the turn must be resumed."""
    return stop_reason == STOP_PAUSE_TURN


def stop_details_from(response: Any) -> dict[str, Any] | None:
    """Read the ``stop_details`` payload off a provider response.

    Anthropic populates it only for a refusal (``category``, ``explanation``).
    Accepts both the SDK's model object and a plain dict, and answers ``None``
    when absent — a test double or an older API version must not break the
    accounting path.

    Args:
        response: The provider's message object.

    Returns:
        dict | None: The details as a plain dict, or ``None``.
    """
    details = getattr(response, "stop_details", None)
    if details is None:
        return None
    if isinstance(details, dict):
        return dict(details)
    payload = {
        key: getattr(details, key)
        for key in ("category", "explanation")
        if isinstance(getattr(details, key, None), str)
    }
    return payload or None


def raise_for_refusal(
    stop_reason: str | None,
    stop_details: dict[str, Any] | None,
    *,
    model: str = "",
) -> None:
    """Raise :class:`LLMRefusalError` when the model declined to answer.

    Args:
        stop_reason: The provider's stop reason.
        stop_details: The refusal payload, when the provider sent one.
        model: Model id, for the log line.

    Raises:
        LLMRefusalError: When ``stop_reason`` is ``refusal``.
    """
    if stop_reason != STOP_REFUSAL:
        return
    details = stop_details or {}
    category = details.get("category")
    explanation = details.get("explanation")
    logger.warning(
        "llm_refusal",
        extra={"model": model, "category": category},
    )
    raise LLMRefusalError(
        category=category if isinstance(category, str) else None,
        explanation=explanation if isinstance(explanation, str) else None,
    )


def apply_stop_reason(
    result: LLMResult, *, model: str = "", allow_refusal: bool = False
) -> LLMResult:
    """Apply the stop-reason policy to a finished result.

    Args:
        result: The result to inspect; mutated in place and returned.
        model: Model id, for the log lines.
        allow_refusal: When True a refusal is returned to the caller instead
            of raised, for callers that want to inspect or report it.

    Returns:
        LLMResult: The same result, with ``truncated`` set when the answer was
        cut short by the output cap.

    Raises:
        LLMRefusalError: On a refusal, unless ``allow_refusal`` is set.
    """
    if result.stop_reason in TRUNCATION_STOP_REASONS:
        result.truncated = True
        logger.warning(
            "llm_response_truncated",
            extra={"model": model, "stop_reason": result.stop_reason},
        )
    elif not allow_refusal:
        raise_for_refusal(result.stop_reason, result.stop_details, model=model)
    return result
