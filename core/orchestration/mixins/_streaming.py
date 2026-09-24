"""The streaming twin of the orchestrated loop.

``process_stream`` used to hand the query straight to the stream handler: no
``LoopBudget`` was bound, so token/USD caps and the per-chunk deadline in the
LLM streaming path never applied; the tenant guard, memory recall, capability
injection and the background memory write were all skipped. ``/chat/stream``
was the one door into the loop with none of its controls.

:func:`stream_with_loop_controls` wraps a stream handler in the same
per-request controls :meth:`ExecutionMixin.process` applies. Durable
checkpointing stays with the non-streaming path: a half-sent stream cannot be
resumed by replaying it, so there is nothing a checkpoint could restore.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from core.observability.logging import get_logger
from core.orchestration.budget_context import activate_budget, deactivate_budget
from core.orchestration.limits import BudgetExceededError, LoopBudget, LoopLimits
from core.orchestration.mixins._agent_attribution import dispatch_attribution
from core.orchestration.mixins._context_assembly import (
    annotate_modality,
    enforce_tenant_isolation,
    inject_capabilities,
    inject_memory_context,
)
from core.orchestration.mixins._failure_paths import handle_budget_exceeded

logger = get_logger(__name__)


async def stream_with_loop_controls(
    orchestrator: Any,
    query: str,
    context: dict[str, Any],
    intent: str,
    handler: Any,
) -> AsyncGenerator[str, None]:
    """Stream *handler*'s answer under the request's loop controls.

    Args:
        orchestrator: The orchestrator (loop limits, memory, capabilities).
        query: The user query.
        context: The request context, enriched in place as ``process`` does.
        intent: The resolved intent.
        handler: The stream handler registered for *intent*.

    Yields:
        Guarded response chunks; on a budget breach, the refusal text.
    """
    from core.orchestration.stream_guard import guard_stream, moderate_stream

    budget = LoopBudget(limits=getattr(orchestrator, "loop_limits", LoopLimits()))
    context["loop_budget"] = budget
    token = activate_budget(budget)
    chunks: list[str] = []
    completed = False
    try:
        enforce_tenant_isolation(context)
        annotate_modality(context)
        await inject_memory_context(orchestrator, query, context, budget)
        inject_capabilities(orchestrator, context, query=query)
        with dispatch_attribution(orchestrator, intent):
            async for chunk in moderate_stream(
                guard_stream(handler.handle(query, context))
            ):
                if isinstance(chunk, str):
                    chunks.append(chunk)
                yield chunk
        completed = True
    except BudgetExceededError as exc:
        refusal = await handle_budget_exceeded(exc, intent, None, None)
        yield refusal["response"]
    finally:
        try:
            deactivate_budget(token)
        except ValueError:
            # The generator was closed from another context (a client
            # disconnect finalised by a different task); the binding died
            # with the context that set it.
            pass

    if completed and getattr(orchestrator, "memory_manager", None):
        orchestrator._schedule_memory_write(query, "".join(chunks), intent)


__all__ = ["stream_with_loop_controls"]
