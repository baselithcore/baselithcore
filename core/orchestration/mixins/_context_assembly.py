"""Request-context assembly for the orchestrator's execution path.

Everything that happens between "a query arrived" and "dispatch it to a flow
handler": the tenant-isolation guard, memory recall, and injection of the
capabilities and skill catalog handlers expect to find on the context.

Split out of :mod:`core.orchestration.mixins.execution` for the module size cap.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.orchestration.limits import LoopBudget

logger = get_logger(__name__)

# Memory context allowance, halved once the request has burned most of its
# token cap so recall can't push the run over the limit.
_CONTEXT_TOKENS = 2000
_CONTEXT_TOKENS_UNDER_PRESSURE = 1000
_TOKEN_PRESSURE_THRESHOLD = 0.8


def enforce_tenant_isolation(context: dict[str, Any]) -> None:
    """Reject a context whose tenant disagrees with the ambient one.

    Prevents cross-tenant leakage when middleware has been bypassed or the
    context has been tampered with. Fills in the tenant when the context
    carries none.

    Raises:
        PermissionError: When the context names a different tenant than the
            ambient request.
    """
    try:
        from core.context import get_current_tenant_id

        ambient_tenant = get_current_tenant_id()
        ctx_tenant = context.get("tenant_id")
        if ctx_tenant is None:
            context["tenant_id"] = ambient_tenant
        elif ambient_tenant is not None and ctx_tenant != ambient_tenant:
            logger.error(
                "tenant_isolation_violation",
                extra={
                    "ambient_tenant": ambient_tenant,
                    "context_tenant": ctx_tenant,
                },
            )
            raise PermissionError("Tenant mismatch in orchestration context")
    except PermissionError:
        raise
    except Exception as e:
        logger.debug(f"Tenant isolation check skipped: {e}")


async def inject_memory_context(
    orchestrator: Any, query: str, context: dict[str, Any], budget: LoopBudget
) -> None:
    """Recall memories and recent history onto *context*.

    Recall and history assembly are independent reads, so they are overlapped
    rather than awaited serially. A memory failure is logged and skipped — it
    degrades the answer but must not fail the request.
    """
    memory_manager = orchestrator.memory_manager
    if not memory_manager:
        return

    try:
        context_tokens = _CONTEXT_TOKENS
        if budget.token_pressure() > _TOKEN_PRESSURE_THRESHOLD:
            context_tokens = _CONTEXT_TOKENS_UNDER_PRESSURE

        if hasattr(memory_manager, "get_context_async"):
            memories, recent_history = await asyncio.gather(
                memory_manager.recall(query, limit=5),
                memory_manager.get_context_async(max_tokens=context_tokens),
            )
        else:
            memories = await memory_manager.recall(query, limit=5)
            recent_history = memory_manager.get_context(max_tokens=context_tokens)

        # Flatten for prompt context
        context["memory_context"] = "\n".join([f"- {m.content}" for m in memories])

        # Context Folding integration: recent conversation history, possibly folded.
        context["recent_history"] = recent_history

        # Also expose the manager itself to agents
        context["memory_manager"] = memory_manager
    except Exception as e:
        logger.warning(f"Memory recall failed: {e}")


def inject_capabilities(orchestrator: Any, context: dict[str, Any]) -> None:
    """Expose the orchestrator's optional capabilities on *context*.

    Includes the declarative skill service plus a prompt-ready catalog — cards
    only, since skill bodies load on activation (progressive disclosure).
    """
    if orchestrator.human_intervention:
        context["human_intervention"] = orchestrator.human_intervention

    if orchestrator.feedback_collector:
        context["feedback_collector"] = orchestrator.feedback_collector

    skill_service = getattr(orchestrator, "skill_service", None)
    if skill_service is not None:
        context["skill_service"] = skill_service
        try:
            catalog = skill_service.render_catalog()
            if catalog:
                context["skills_catalog"] = catalog
        except Exception as e:
            logger.warning(f"Skill catalog rendering failed: {e}")


__all__ = [
    "enforce_tenant_isolation",
    "inject_capabilities",
    "inject_memory_context",
]
