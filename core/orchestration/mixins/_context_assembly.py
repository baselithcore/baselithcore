"""Request-context assembly for the orchestrator's execution path.

Everything that happens between "a query arrived" and "dispatch it to a flow
handler": the tenant-isolation guard, memory recall, and injection of the
capabilities and skill catalog handlers expect to find on the context.

Split out of :mod:`core.orchestration.mixins.execution` for the module size cap.
"""

from __future__ import annotations

import asyncio
import inspect
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


def _accepts_query(fn: Any) -> bool:
    """Whether ``fn`` takes a ``query`` keyword (query-aware context assembly).

    ``HierarchicalMemory.get_context`` gates its Background and Long-term
    sections by relevance to the current request when given one; managers
    whose signature predates that keyword are called as before.
    """
    try:
        return "query" in inspect.signature(fn).parameters
    except (TypeError, ValueError):  # builtins, C callables, mocks
        return False


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
            get_context = memory_manager.get_context
            # Hand the request to a query-aware manager so its Background and
            # Long-term sections are gated by relevance rather than recency.
            recent_history = (
                get_context(max_tokens=context_tokens, query=query)
                if _accepts_query(get_context)
                else get_context(max_tokens=context_tokens)
            )

        # Flatten for prompt context
        context["memory_context"] = "\n".join([f"- {m.content}" for m in memories])

        # Context Folding integration: recent conversation history, possibly folded.
        context["recent_history"] = recent_history

        # Record how much of the request's budget went to static recall rather
        # than dynamic reasoning. Measurement only — never charged against the
        # token cap (see LoopBudget.record_context_tokens).
        _record_context_allocation(budget, context["memory_context"], recent_history)

        # Also expose the manager itself to agents
        context["memory_manager"] = memory_manager
    except Exception as e:
        logger.warning(f"Memory recall failed: {e}")


def _record_context_allocation(
    budget: LoopBudget, memory_context: str, recent_history: str
) -> None:
    """Label the budget with the size of the context assembled for this request."""
    try:
        from core.utils.tokens import estimate_tokens

        total = estimate_tokens(memory_context) + estimate_tokens(recent_history)
        budget.record_context_tokens(total)
    except Exception as e:  # pragma: no cover - measurement must never fail a run
        logger.debug(f"Context allocation measurement skipped: {e}")


def annotate_modality(context: dict[str, Any]) -> None:
    """Stamp a modality hint onto *context* from any attachment material.

    Runs before intent classification so handlers (and future
    classification hints) can branch on ``context["modality"]`` without
    re-sniffing bytes. Detection sources, in trust order: raw
    ``attachment_data`` bytes (magic-byte sniff), the declared
    ``attachment_mime``, then ``attachment_name`` or the first
    ``image_paths`` entry (extension). Plain ``image_data`` base64 payloads
    are images by the vision surface's contract. A context without
    attachment material stays unannotated — plain text queries carry no
    ``modality`` key — and an existing annotation is never overwritten.
    """
    if "modality" in context:
        return
    data = context.get("attachment_data")
    filename = context.get("attachment_name")
    mime = context.get("attachment_mime")
    paths = context.get("image_paths")
    if filename is None and isinstance(paths, (list, tuple)) and paths:
        filename = str(paths[0])
    if data is None and filename is None and mime is None:
        if context.get("image_data"):
            context["modality"] = "image"
        return

    from core.orchestration.modality_router import annotate_context

    annotate_context(
        context,
        data if isinstance(data, bytes) else None,
        filename=filename if isinstance(filename, str) else None,
        mime=mime if isinstance(mime, str) else None,
    )


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
    "annotate_modality",
    "enforce_tenant_isolation",
    "inject_capabilities",
    "inject_memory_context",
]
