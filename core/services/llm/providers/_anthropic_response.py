"""Response-side policy for the Anthropic provider.

The mirror of ``_anthropic_request``: that module decides what a request may
contain, this one decides what a response *means*. One function today — the
refusal/truncation policy the plain-text path has to apply itself, because its
``(text, tokens)`` return type has nowhere to carry a stop reason.

Kept out of the provider module for the file-size cap; pure, so it tests
without a client.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger
from core.services.llm.stop_reasons import (
    TRUNCATION_STOP_REASONS,
    raise_for_refusal,
    stop_details_from,
)

logger = get_logger(__name__)

__all__ = ["check_stop_reason"]


def check_stop_reason(response: Any, *, model: str, kwargs: dict[str, Any]) -> None:
    """Apply the refusal/truncation policy to a plain-text generation.

    The two stop reasons that change what an answer *means* are acted on here:
    a refusal raises (unless the caller opted in to receiving it) and a
    truncated answer is logged, never returned as if complete.

    Args:
        response: The provider response.
        model: The model that produced it (for the log line and the error).
        kwargs: Caller kwargs (``allow_refusal`` is read).

    Raises:
        LLMRefusalError: The model declined and ``allow_refusal`` is not set.
    """
    stop_reason = getattr(response, "stop_reason", None)
    if not kwargs.get("allow_refusal", False):
        raise_for_refusal(stop_reason, stop_details_from(response), model=model)
    if isinstance(stop_reason, str) and stop_reason in TRUNCATION_STOP_REASONS:
        logger.warning(
            "llm_response_truncated",
            extra={"model": model, "stop_reason": stop_reason},
        )
