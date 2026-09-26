"""Input guard for the chat service's non-streaming routes."""

from __future__ import annotations

from core.services.chat.exceptions import ChatServiceError


def validate_input(query: str) -> None:
    """Run the regex input guard on the shared, compiled instance.

    Kept on the non-streaming routes even though ``Orchestrator.process``
    guards too: it still holds when the orchestrator pipeline is switched off
    (``BASELITH_ORCHESTRATOR_GUARDRAILS``) or a custom agent is injected, and
    it preserves this surface's contract of raising :class:`ChatServiceError`.
    It used to build a fresh ``InputGuard`` per request, recompiling every
    pattern; the instance is now the one the orchestrator pipeline caches.

    Args:
        query: The user query.

    Raises:
        ChatServiceError: If the query is blocked.
    """
    from core.orchestration.guard_pipeline import get_input_guard

    verdict = get_input_guard().validate(query)
    if not verdict.is_valid:
        reason = verdict.blocked_reason or "Potentially harmful content detected"
        raise ChatServiceError(f"Blocked by InputGuard: {reason}")


__all__ = ["validate_input"]
