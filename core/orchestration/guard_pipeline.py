"""Guardrails pipeline for the orchestration loop.

Wires :mod:`core.guardrails` into ``Orchestrator.process`` so every request —
not just the chat surface — passes through input validation on the way in and
output filtering (PII redaction, harmful-content patterns) on the way out.

The input check is the synchronous regex path only: it runs before any budget
is spent and adds microseconds, never an LLM call. The optional LLM-backed
classifier (``InputGuard.validate_async``) stays a chat-surface concern.

Enabled by default; set ``BASELITH_ORCHESTRATOR_GUARDRAILS=0/false/no/off``
to bypass both directions (e.g. for trusted internal batch traffic).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

_ENV = "BASELITH_ORCHESTRATOR_GUARDRAILS"


def _enabled() -> bool:
    """Whether the orchestrator-level guard pipeline is active (default on)."""
    return os.environ.get(_ENV, "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


@lru_cache(maxsize=1)
def _guards() -> tuple[Any, Any]:
    """Build the guard pair once (compiled regexes are reused across calls)."""
    from core.guardrails.input_guard import InputGuard
    from core.guardrails.output_guard import OutputGuard

    return InputGuard(), OutputGuard()


def guard_input(query: str) -> dict[str, Any] | None:
    """Validate an inbound query. ``None`` = pass; a result dict = blocked.

    The blocked dict matches the orchestrator's result shape (``response`` /
    ``intent`` / ``error``) so callers and API surfaces render it like any
    other outcome instead of crashing on an exception.
    """
    if not _enabled():
        return None
    input_guard, _ = _guards()
    verdict = input_guard.validate(query)
    if verdict.is_valid:
        return None
    reason = verdict.blocked_reason or "potentially harmful content"
    logger.warning(
        "orchestrator_input_blocked",
        extra={"reason": reason, "patterns": verdict.detected_patterns},
    )
    return {
        "response": f"Request blocked by input guardrails: {reason}",
        "intent": "blocked_by_guardrails",
        "error": True,
    }


async def guard_input_async(query: str) -> dict[str, Any] | None:
    """Async inbound guard: regex validation first, then content moderation.

    The synchronous :func:`guard_input` (microseconds, no network) always runs
    first — a regex-blocked query never spends a moderation call. Moderation
    itself runs only when the guard pipeline is enabled, a moderator is
    configured (``BASELITH_MODERATION_PROVIDER``) and
    ``GuardrailsConfig.moderation_enabled`` is on. Moderator failures are
    fail-open: an outage degrades to unmoderated service, never to a chat
    outage.
    """
    blocked = guard_input(query)
    if blocked is not None:
        return blocked
    if not _enabled():
        return None

    from core.guardrails import moderation

    config = moderation.get_guardrails_config()
    if not config.moderation_enabled:
        return None
    moderator = moderation.get_moderator()
    if moderator is None:
        return None
    try:
        verdict = await moderator.moderate(query)
    except Exception as exc:
        logger.warning("moderation_unavailable_fail_open", extra={"error": str(exc)})
        return None
    if not verdict.flagged:
        return None
    logger.warning(
        "orchestrator_input_blocked_moderation",
        extra={
            "provider": verdict.provider,
            "categories": sorted(verdict.categories),
        },
    )
    return {
        "response": "Request blocked by content moderation.",
        "intent": "blocked_by_moderation",
        "error": True,
    }


def guard_output(result: dict[str, Any]) -> dict[str, Any]:
    """Filter the outbound ``response`` text in a result dict (in place).

    PII is redacted and harmful patterns filtered via ``OutputGuard``. Any
    redaction counts are surfaced under ``result["guardrails"]["redactions"]``
    so callers can observe that filtering occurred.
    """
    if not _enabled():
        return result
    response = result.get("response")
    if not isinstance(response, str) or not response:
        return result
    _, output_guard = _guards()
    filtered = output_guard.filter(response)
    if filtered.filtered_output != response:
        result["response"] = filtered.filtered_output
        meta = result.setdefault("guardrails", {})
        if filtered.redactions:
            meta["redactions"] = filtered.redactions
        if filtered.warnings:
            meta["warnings"] = filtered.warnings
        logger.info(
            "orchestrator_output_filtered",
            extra={"redactions": filtered.redactions},
        )
    return result
