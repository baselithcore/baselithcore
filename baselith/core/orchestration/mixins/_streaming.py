"""The streaming twin of the orchestrated loop.

``process_stream`` used to hand the query straight to the stream handler: no
``LoopBudget`` was bound, so token/USD caps and the per-chunk deadline in the
LLM streaming path never applied; the tenant guard, memory recall, capability
injection and the background memory write were all skipped. ``/chat/stream``
was the one door into the loop with none of its controls.

:func:`stream_with_loop_controls` wraps a stream handler in the same
per-request controls :meth:`ExecutionMixin.process` applies.

**Durable streams.** With a checkpoint store configured, a streamed run is a
checkpointed run too: it gets a run id (the caller's, or a generated one), a
checkpoint created — or, with ``resume``, claimed — under the run's lease and
heartbeat, and the manager is exposed as ``context["checkpoint"]`` and bound as
the ambient checkpoint while the handler streams. Every durable unit the
handler starts (a typed agent's model turns and tool calls, a crew, a search)
therefore records, and a resumed stream replays them instead of paying again.

What a stream cannot do is resume *mid-generation*: a token stream cut by a
crash cannot be continued from where it stopped — the provider's generation
is gone, and splicing a regenerated answer onto the text already delivered
would produce something no model wrote. So the delivered answer is recorded
once, when the stream completes (one write, after the last chunk; the chunk
boundaries ride along under :data:`STREAM_CHUNKS_KEY` so a replay re-emits the
very same events), and an interrupted stream resumed later regenerates its
answer on top of the replayed steps. See
:mod:`baselith.core.orchestration.stream_runs` for how an entry point decides
between replaying, resuming and starting fresh.

A stream the client abandons or the process cancels is recorded like a
cancelled ``process()``: ``failed`` with ``cancelled`` (resumable by a retry
under the same id), or — while draining — left ``running`` with the yielded
marker and its lease released, so another replica can pick it up.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import AbstractContextManager, aclosing
from typing import Any

from baselith.core.observability.logging import get_logger
from baselith.core.orchestration.budget_context import (
    activate_budget,
    deactivate_budget,
)
from baselith.core.orchestration.checkpoint_errors import CheckpointConflictError
from baselith.core.orchestration.checkpoint_lease import end_checkpoint_attempt
from baselith.core.orchestration.checkpoint_scope import bind_checkpoint
from baselith.core.orchestration.limits import (
    BudgetExceededError,
    LoopBudget,
    LoopLimits,
)
from baselith.core.orchestration.mixins._agent_attribution import dispatch_attribution
from baselith.core.orchestration.mixins._context_assembly import (
    annotate_modality,
    enforce_tenant_isolation,
    inject_capabilities,
    inject_memory_context,
    rag_only_intent,
)
from baselith.core.orchestration.mixins._failure_paths import (
    handle_budget_exceeded,
    handle_cancellation,
)
from baselith.core.orchestration.mixins._flow_events import (
    build_flow_completed,
    emit_flow_completed,
)

logger = get_logger(__name__)

#: ``Checkpoint.plugin_data`` key holding the lengths of a completed stream's
#: chunks, so a replay re-emits the same events (``answer`` holds the text).
STREAM_CHUNKS_KEY = "stream_chunks"

#: What a stream says when its handler fails mid-answer.
STREAM_ERROR_MESSAGE = "[ERROR] The answer could not be completed. Please retry."


async def run_stream(
    orchestrator: Any,
    query: str,
    context: dict[str, Any] | None,
    intent: str | None,
    run_id: str | None,
    resume: bool,
) -> AsyncGenerator[str, None]:
    """Body of :meth:`ExecutionMixin.process_stream` (module size cap)."""
    from baselith.core.orchestration.guard_pipeline import guard_input_async

    # Input guardrails (regex + optional content moderation) run before any
    # classification/LLM spend, same as the non-streaming path.
    blocked = await guard_input_async(query)
    if blocked is not None:
        yield blocked["response"]
        return

    context = context if context is not None else {}
    store = getattr(orchestrator, "checkpoint_store", None)
    if not intent and resume and run_id and store is not None:
        # A resumed run keeps the intent it was classified with.
        recorded = await store.load(run_id)
        intent = recorded.intent if recorded is not None else None
    # Classify intent if not provided (rag_only pins the default intent)
    intent = intent or rag_only_intent(orchestrator, context)
    if not intent:
        intent = await orchestrator.classify_intent_async(query)
    context["intent"] = intent

    handler = orchestrator._stream_handlers.get(intent)
    if not handler:
        # No streaming handler: run the full non-streaming pipeline (memory,
        # budget, checkpoint, output guard) and emit its final response as one
        # chunk — a real answer delivered late instead of a placeholder
        # delivered never.
        logger.debug(
            f"No stream handler for intent: {intent}, "
            "falling back to non-streaming process()"
        )
        durable: dict[str, Any] = {}
        if run_id:
            durable = {"run_id": run_id, "resume": resume}
        result = await orchestrator.process(query, context, intent, **durable)
        response = result.get("response", "")
        if response:
            yield response
        return

    # Execute streaming handler under the same per-request controls as
    # process() — budget, tenant guard, memory recall and write, checkpoint —
    # bound to its owning plugin, with chunks through the streaming output
    # guard and the opt-in moderation layer.
    try:
        # aclosing: a consumer that leaves early closes the whole chain now,
        # in its own context, so the run's cancellation is recorded and its
        # lease released — not whenever the garbage collector gets to it.
        async with aclosing(
            stream_with_loop_controls(
                orchestrator,
                query,
                context,
                intent,
                handler,
                run_id=run_id,
                resume=resume,
            )
        ) as stream:
            async for chunk in stream:
                yield chunk
    except Exception as e:
        # The client gets a generic line: the exception text can carry
        # provider URLs, SQL or file paths, and the log already has it.
        logger.error(f"Stream handler error for intent {intent}: {e}", exc_info=True)
        yield STREAM_ERROR_MESSAGE


async def _open_checkpoint(
    orchestrator: Any,
    query: str,
    context: dict[str, Any],
    intent: str,
    budget: LoopBudget,
    run_id: str | None,
    resume: bool,
) -> Any | None:
    """Create or claim the stream's checkpoint; ``None`` without a store."""
    store = getattr(orchestrator, "checkpoint_store", None)
    if store is None:
        return None
    from baselith.core.orchestration.checkpoint_init import init_checkpoint

    manager = await init_checkpoint(
        store, query, context, intent, budget, run_id, resume
    )
    manager.start_heartbeat()
    if manager.checkpoint.intent is None:
        manager.checkpoint.intent = intent
    return manager


async def _record_completion(manager: Any, chunks: list[str], budget: Any) -> None:
    """Mark the run completed with the delivered answer and its chunking."""
    manager.checkpoint.plugin_data[STREAM_CHUNKS_KEY] = [len(c) for c in chunks]
    manager.update_budget(budget.snapshot())
    try:
        await manager.complete("".join(chunks))
    except CheckpointConflictError as exc:
        # The answer is already delivered; another executor owns the record.
        logger.warning("stream_completion_not_recorded error=%s", exc)


async def _close_quietly(stream: Any) -> None:
    """Close one stage of the pipeline; a failing close never masks the exit."""
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception as exc:
        logger.debug("stream_stage_close_failed error=%s", exc)


def _bind(manager: Any | None) -> AbstractContextManager[Any] | None:
    """Bind ``manager`` as the ambient checkpoint across the stream's yields."""
    if manager is None:
        return None
    binding = bind_checkpoint(manager)
    binding.__enter__()
    return binding


def _unbind(binding: AbstractContextManager[Any] | None) -> None:
    if binding is None:
        return
    try:
        binding.__exit__(None, None, None)
    except ValueError:
        # Closed from another context (a disconnect finalised by a different
        # task): the binding died with the context that set it.
        pass


async def stream_with_loop_controls(
    orchestrator: Any,
    query: str,
    context: dict[str, Any],
    intent: str,
    handler: Any,
    *,
    run_id: str | None = None,
    resume: bool = False,
) -> AsyncGenerator[str, None]:
    """Stream *handler*'s answer under the request's loop controls.

    Args:
        orchestrator: The orchestrator (loop limits, memory, capabilities,
            checkpoint store).
        query: The user query.
        context: The request context, enriched in place as ``process`` does.
        intent: The resolved intent.
        handler: The stream handler registered for *intent*.
        run_id: The run's id (generated when omitted and a store is set).
        resume: Claim and continue the existing checkpoint of ``run_id``.

    Yields:
        Guarded response chunks; on a budget breach, the refusal text.
    """
    from baselith.core.orchestration.stream_guard import guard_stream, moderate_stream

    budget = LoopBudget(limits=getattr(orchestrator, "loop_limits", LoopLimits()))
    context["loop_budget"] = budget
    token = activate_budget(budget)
    start_time = time.time()
    chunks: list[str] = []
    completed = False
    manager: Any | None = None
    bound: AbstractContextManager[Any] | None = None
    # handler → output guard → moderation; closed outermost first on exit,
    # since a wrapper's own close never closes the generator it iterates.
    pipeline: list[Any] = []
    try:
        enforce_tenant_isolation(context)
        annotate_modality(context)
        # The checkpoint write overlaps memory recall instead of adding a
        # round trip ahead of the first token. Both outcomes are collected
        # before either error is raised, so an opened checkpoint is always
        # handed to the cleanup below (its lease released, its failure kept).
        outcomes: list[Any] = await asyncio.gather(
            _open_checkpoint(
                orchestrator, query, context, intent, budget, run_id, resume
            ),
            inject_memory_context(orchestrator, query, context, budget),
            return_exceptions=True,
        )
        opened, recalled = outcomes
        if isinstance(opened, BaseException):
            raise opened
        manager = opened
        if manager is not None:
            context["checkpoint"] = manager
            run_id = manager.run_id
        if isinstance(recalled, BaseException):
            raise recalled
        inject_capabilities(orchestrator, context, query=query)
        with dispatch_attribution(orchestrator, intent):
            bound = _bind(manager)
            pipeline.append(handler.handle(query, context))
            pipeline.append(guard_stream(pipeline[-1]))
            pipeline.append(moderate_stream(pipeline[-1]))
            async for chunk in pipeline[-1]:
                if isinstance(chunk, str):
                    chunks.append(chunk)
                yield chunk
        completed = True
        if manager is not None:
            await _record_completion(manager, chunks, budget)
    except BudgetExceededError as exc:
        refusal = await handle_budget_exceeded(exc, intent, manager, run_id)
        yield refusal["response"]
    except (asyncio.CancelledError, GeneratorExit):
        # The client left or the process is draining: record it (shielded),
        # then let the cancellation/close proceed.
        await handle_cancellation(intent, manager, run_id)
        raise
    except Exception as exc:
        if manager is not None:
            try:
                await asyncio.shield(manager.fail(str(exc)))
            except Exception as cp_err:
                logger.warning("stream_failure_not_recorded error=%s", cp_err)
        raise
    finally:
        for stage in reversed(pipeline):
            await _close_quietly(stage)
        _unbind(bound)
        await end_checkpoint_attempt(manager)
        try:
            deactivate_budget(token)
        except ValueError:
            # The generator was closed from another context (a client
            # disconnect finalised by a different task); the binding died
            # with the context that set it.
            pass

    if not completed:
        return
    answer = "".join(chunks)
    if getattr(orchestrator, "memory_manager", None):
        orchestrator._schedule_memory_write(query, answer, intent)
    # The streamed answer completes a flow like any other: without the event
    # online evaluation never saw streamed traffic. Chunks were guarded on the
    # way out, so ``answer`` is the text the user received.
    if run_id is None:
        recorded_id = context.get("run_id")
        run_id = recorded_id if isinstance(recorded_id, str) else None
    emit_flow_completed(
        build_flow_completed(
            intent=intent,
            query=query,
            response=answer,
            context=context,
            start_time=start_time,
            run_id=run_id,
            budget=budget,
            streamed=True,
        )
    )


__all__ = [
    "STREAM_CHUNKS_KEY",
    "STREAM_ERROR_MESSAGE",
    "run_stream",
    "stream_with_loop_controls",
]
