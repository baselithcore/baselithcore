"""The ``FLOW_COMPLETED`` payload, built once for both orchestrated paths.

Online evaluation scores what the user actually received, so the success event
is emitted **after** the output guard: the ``response`` it carries is the
guarded text, and ``guardrails`` holds the guard's verdicts (redaction counts,
moderation and groundedness outcomes). :meth:`ExecutionMixin._process_with_budget`
therefore only *stages* the payload on the request context under
:data:`FLOW_EVENT_KEY`; :meth:`ExecutionMixin.process` completes and emits it
once the guard has run. The streaming path emits directly: its chunks are
guarded as they leave the handler.

The payload also carries the ambient ``tenant_id`` and the run's spend
(``cost_usd``, ``tokens``): subscribers handle the event outside the request
(queued, on another task), where the tenant context and the budget are gone.
"""

from __future__ import annotations

import time
from typing import Any

from baselith.core.context import get_tenant_or_default
from baselith.core.events import EventNames, get_event_bus
from baselith.core.observability.logging import get_logger

logger = get_logger(__name__)

#: Request-context key under which the staged success payload waits for the
#: output guard. Popped by :func:`emit_staged_flow_completed`.
FLOW_EVENT_KEY = "_flow_completed_event"


def _safe_context(context: dict[str, Any]) -> dict[str, Any]:
    """The JSON-friendly part of the request context (no live objects)."""
    return {
        k: v
        for k, v in context.items()
        if isinstance(v, (str, int, float, bool, list, dict))
        and k not in ("memory_manager", FLOW_EVENT_KEY)
    }


def build_flow_completed(
    *,
    intent: str,
    query: str,
    response: str,
    context: dict[str, Any],
    start_time: float,
    run_id: str | None,
    budget: Any | None = None,
    success: bool = True,
    streamed: bool = False,
) -> dict[str, Any]:
    """Assemble a ``FLOW_COMPLETED`` payload for a finished flow.

    Args:
        intent: The resolved intent.
        query: The user query.
        response: The answer text (replaced by the guarded text when staged).
        context: The request context; only its plain-data entries are copied.
        start_time: ``time.time()`` when the handler started.
        run_id: The run id, when the run is addressable.
        budget: The request's ``LoopBudget``; its spend is copied when given.
        success: Whether the handler produced an answer.
        streamed: Whether the answer was streamed.

    Returns:
        The event payload.
    """
    payload: dict[str, Any] = {
        "intent": intent,
        "query": query,
        "response": response,
        "context": _safe_context(context),
        "duration_ms": int((time.time() - start_time) * 1000),
        "success": success,
        "run_id": run_id,
        "tenant_id": get_tenant_or_default(),
    }
    if streamed:
        payload["streamed"] = True
    if budget is not None:
        try:
            snapshot = budget.snapshot()
            payload["cost_usd"] = float(snapshot.cost_usd)
            payload["tokens"] = int(snapshot.tokens)
        except Exception as exc:  # spend is informative; never lose the event
            logger.debug("flow_event_budget_unavailable: %s", exc)
    return payload


def emit_flow_completed(payload: dict[str, Any]) -> None:
    """Emit ``FLOW_COMPLETED``; a bus failure is logged, never raised."""
    try:
        get_event_bus().emit_sync(EventNames.FLOW_COMPLETED, payload)
    except Exception as exc:
        logger.warning(f"Failed to emit completion event: {exc}")


def emit_staged_flow_completed(
    context: dict[str, Any], result: dict[str, Any] | None
) -> None:
    """Pop the staged payload and emit it with the guarded *result*.

    Args:
        context: The request context the payload was staged on.
        result: The guarded result, or ``None`` when the guard raised — the
            staged payload is then discarded so it never leaks into a reused
            context.
    """
    payload = context.pop(FLOW_EVENT_KEY, None)
    if not isinstance(payload, dict) or result is None:
        return
    payload["response"] = result.get("response", "")
    guardrails = result.get("guardrails")
    if isinstance(guardrails, dict) and guardrails:
        payload["guardrails"] = dict(guardrails)
    emit_flow_completed(payload)


__all__ = [
    "FLOW_EVENT_KEY",
    "build_flow_completed",
    "emit_flow_completed",
    "emit_staged_flow_completed",
]
