"""Guardrails pipeline for the orchestration loop.

Wires :mod:`core.guardrails` into ``Orchestrator.process`` so every request —
not just the chat surface — passes through input validation on the way in and
output filtering (PII redaction, harmful-content patterns) on the way out.

The always-on input check is the synchronous regex path: it runs before any
budget is spent and adds microseconds, never an LLM call. On top of it sit
two opt-in async layers — content moderation
(``BASELITH_MODERATION_PROVIDER``) and the LLM intent taxonomy
(``BASELITH_INPUT_GUARD_TAXONOMY``); the chat surface's binary LLM check
(``InputGuard.validate_async``) stays a chat-surface concern. Outbound, the
opt-in groundedness rail (``BASELITH_OUTPUT_GROUNDEDNESS``) and output
moderation (``BASELITH_MODERATION_OUTPUT``) layer on the same way.

Enabled by default; set ``BASELITH_ORCHESTRATOR_GUARDRAILS=0/false/no/off``
to bypass both directions (e.g. for trusted internal batch traffic).
"""

from __future__ import annotations

import os
import time
from functools import lru_cache
from typing import Any

from core.observability.logging import get_logger
from core.observability.metrics import (
    GUARDRAIL_BLOCKS_TOTAL,
    GUARDRAIL_LATENCY_SECONDS,
    GUARDRAIL_REDACTIONS_TOTAL,
)

logger = get_logger(__name__)


def _pattern_reason(patterns: list[str] | None) -> str:
    """Low-cardinality reason slug: the family prefix of the first pattern."""
    if not patterns:
        return "blocked"
    head = patterns[0]
    return head.split(":", 1)[0] if ":" in head else "blocked"


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
    started = time.perf_counter()
    verdict = input_guard.validate(query)
    GUARDRAIL_LATENCY_SECONDS.labels(layer="input_regex").observe(
        time.perf_counter() - started
    )
    if verdict.is_valid:
        return None
    reason = verdict.blocked_reason or "potentially harmful content"
    GUARDRAIL_BLOCKS_TOTAL.labels(
        layer="input_regex", reason=_pattern_reason(verdict.detected_patterns)
    ).inc()
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
    """Async inbound guard: regex, then moderation, then the LLM taxonomy.

    The synchronous :func:`guard_input` (microseconds, no network) always runs
    first — a regex-blocked query never spends a moderation or taxonomy call.
    Moderation runs only when the guard pipeline is enabled, a moderator is
    configured (``BASELITH_MODERATION_PROVIDER``) and
    ``GuardrailsConfig.moderation_enabled`` is on. The taxonomy rail
    (``BASELITH_INPUT_GUARD_TAXONOMY``, default off) classifies whatever
    passed both layers via ``InputGuard.classify`` and blocks
    jailbreak/harmful — plus out_of_scope under a configured topical rail —
    at or above the confidence threshold. Moderator and classifier failures
    are fail-open: an outage degrades to unguarded service, never to a chat
    outage.
    """
    blocked = guard_input(query)
    if blocked is not None:
        return blocked
    if not _enabled():
        return None
    blocked = await _moderate_input(query)
    if blocked is not None:
        return blocked
    return await _classify_input_taxonomy(query)


async def _moderate_input(query: str) -> dict[str, Any] | None:
    """Content-moderation layer of :func:`guard_input_async` (fail-open)."""
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
    GUARDRAIL_BLOCKS_TOTAL.labels(
        layer="input_moderation",
        reason=next(iter(sorted(verdict.categories)), "flagged"),
    ).inc()
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


_TAXONOMY_ENV = "BASELITH_INPUT_GUARD_TAXONOMY"
_TAXONOMY_THRESHOLD_ENV = "BASELITH_INPUT_GUARD_TAXONOMY_THRESHOLD"
_TAXONOMY_THRESHOLD_DEFAULT = 0.8


def _taxonomy_enabled() -> bool:
    """Opt-in switch for the LLM input taxonomy (one LLM call per request)."""
    return os.environ.get(_TAXONOMY_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _taxonomy_threshold() -> float:
    """Confidence at/over which a blockable intent blocks (default 0.8)."""
    try:
        return float(os.environ.get(_TAXONOMY_THRESHOLD_ENV, ""))
    except ValueError:
        return _TAXONOMY_THRESHOLD_DEFAULT


async def _classify_input_taxonomy(query: str) -> dict[str, Any] | None:
    """LLM taxonomy layer of :func:`guard_input_async` (opt-in, fail-open).

    Blocks ``jailbreak`` and ``harmful`` always; ``out_of_scope`` only when
    ``GuardrailsConfig.allowed_topics`` defines a topical rail — without one,
    ``InputGuard.classify`` never returns it. Sub-threshold confidence passes.
    """
    if not _taxonomy_enabled():
        return None
    input_guard, _ = _guards()
    started = time.perf_counter()
    classification = await input_guard.classify(query)
    GUARDRAIL_LATENCY_SECONDS.labels(layer="input_taxonomy").observe(
        time.perf_counter() - started
    )
    blockable = {"jailbreak", "harmful"}
    if getattr(input_guard.config, "allowed_topics", None):
        blockable.add("out_of_scope")
    if classification.intent not in blockable:
        return None
    if classification.confidence < _taxonomy_threshold():
        return None
    GUARDRAIL_BLOCKS_TOTAL.labels(
        layer="input_taxonomy", reason=classification.intent
    ).inc()
    logger.warning(
        "orchestrator_input_blocked_taxonomy",
        extra={
            "taxonomy_intent": classification.intent,
            "confidence": classification.confidence,
        },
    )
    return {
        "response": (
            "Request blocked by input guardrails: classified as "
            f"{classification.intent.replace('_', ' ')}."
        ),
        "intent": "blocked_by_taxonomy",
        "error": True,
    }


def _output_moderation_enabled() -> bool:
    """Opt-in switch for output-side moderation (one extra call per response)."""
    return os.environ.get("BASELITH_MODERATION_OUTPUT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


async def guard_output_async(result: dict[str, Any]) -> dict[str, Any]:
    """Async outbound guard: PII filter, groundedness, content moderation.

    :func:`guard_output` (redaction, harmful patterns) always applies first.
    The groundedness rail (``BASELITH_OUTPUT_GROUNDEDNESS``, default off)
    then judges a sourced response against its retrieved material — see
    :mod:`core.orchestration.guard_groundedness`. Moderation of the final
    response is a further opt-in on top of the provider gate
    (``BASELITH_MODERATION_OUTPUT``) because it spends one moderation call
    per response; a flagged response is replaced wholesale and the
    categories surfaced under ``result["guardrails"]["moderation"]``.
    Judge and moderator failures are fail-open.
    """
    result = guard_output(result)
    if not _enabled():
        return result

    from core.orchestration.guard_groundedness import apply_groundedness

    result = await apply_groundedness(result)

    if not _output_moderation_enabled():
        return result
    response = result.get("response")
    if not isinstance(response, str) or not response:
        return result

    from core.guardrails import moderation

    if not moderation.get_guardrails_config().moderation_enabled:
        return result
    moderator = moderation.get_moderator()
    if moderator is None:
        return result
    try:
        verdict = await moderator.moderate(response)
    except Exception as exc:
        logger.warning(
            "output_moderation_unavailable_fail_open", extra={"error": str(exc)}
        )
        return result
    if verdict.flagged:
        GUARDRAIL_BLOCKS_TOTAL.labels(
            layer="output_moderation",
            reason=next(iter(sorted(verdict.categories)), "flagged"),
        ).inc()
        logger.warning(
            "orchestrator_output_blocked_moderation",
            extra={
                "provider": verdict.provider,
                "categories": sorted(verdict.categories),
            },
        )
        result["response"] = "Response blocked by content moderation."
        meta = result.setdefault("guardrails", {})
        meta["moderation"] = {
            "blocked": True,
            "provider": verdict.provider,
            "categories": sorted(verdict.categories),
        }
    return result


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
    started = time.perf_counter()
    filtered = output_guard.filter(response)
    GUARDRAIL_LATENCY_SECONDS.labels(layer="output_pii").observe(
        time.perf_counter() - started
    )
    if filtered.redactions:
        total = (
            sum(filtered.redactions.values())
            if isinstance(filtered.redactions, dict)
            else len(filtered.redactions)
        )
        GUARDRAIL_REDACTIONS_TOTAL.labels(layer="output_pii").inc(max(1, total))
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
