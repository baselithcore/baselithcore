"""Execution Mixin for Orchestrator."""

import asyncio
import contextvars
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, Optional

from core.context import reset_plugin_context, set_plugin_context
from core.observability.logging import get_logger
from core.orchestration.autonomy import ApprovalPendingError
from core.orchestration.limits import (
    BudgetExceededError,
    LoopBudget,
    LoopLimits,
)
from core.orchestration.mixins._context_assembly import (
    enforce_tenant_isolation,
    inject_capabilities,
    inject_memory_context,
)
from core.orchestration.run_events import EventType, publish_run_event

try:
    from core.events import EventNames, get_event_bus

    _HAS_EVENT_BUS = True
except ImportError:
    _HAS_EVENT_BUS = False

if TYPE_CHECKING:
    from core.human import HumanIntervention
    from core.learning import FeedbackCollector
    from core.memory import AgentMemory
    from core.orchestration.autonomy import AutonomyPolicy
    from core.orchestration.checkpoint import CheckpointManager, CheckpointStore
    from core.orchestration.contract import ContractValidator
    from core.orchestration.protocols import FlowHandler, StreamHandler

logger = get_logger(__name__)


class ExecutionMixin:
    """Mixin for query execution and processing."""

    memory_manager: Optional["AgentMemory"]
    human_intervention: Optional["HumanIntervention"]
    feedback_collector: Optional["FeedbackCollector"]
    loop_limits: LoopLimits
    contract_validator: Optional["ContractValidator"]
    autonomy_policy: "AutonomyPolicy"
    checkpoint_store: Optional["CheckpointStore"]
    _flow_handlers: dict[str, "FlowHandler"]
    _stream_handlers: dict[str, "StreamHandler"]
    # References to in-flight background memory writes: fire-and-forget tasks
    # without a reference can be garbage-collected mid-run.
    _memory_write_tasks: set
    # Bounds concurrent background memory writes (lazy-init on first write).
    _memory_write_sem: "asyncio.Semaphore | None"

    def _bind_intent_plugin(self, intent: str) -> contextvars.Token | None:
        """Bind the plugin owning *intent*'s handler to the plugin context.

        Lets downstream seams (e.g. the central per-plugin LLM policy)
        attribute the dispatch to its owning plugin. Returns the reset token,
        or ``None`` when the intent is core-owned/unknown. Best-effort:
        attribution failures never block a dispatch.
        """
        registry = getattr(self, "plugin_registry", None)
        if registry is None:
            return None
        try:
            owner = registry.get_flow_handler_owner(intent)
        except Exception:
            return None
        return set_plugin_context(owner) if owner else None

    def _schedule_memory_write(
        self, query: str, response_text: str, intent: str | None
    ) -> None:
        """Persist the interaction to memory off the request path.

        Body in :func:`core.orchestration.mixins._memory_write.schedule_memory_write`
        (extracted for the module size cap).
        """
        from core.orchestration.mixins._memory_write import schedule_memory_write

        schedule_memory_write(self, query, response_text, intent)

    # This is provided by IntentMixin
    async def classify_intent_async(self, query: str) -> str:
        """
        Internal placeholder for intent classification.

        This method should be implemented by the IntentMixin or an
        equivalent provider in the concrete Orchestrator class.

        Args:
            query: The user input to classify.

        Returns:
            str: The identifier of the classified intent.

        Raises:
            NotImplementedError: If not overridden by a mixin.
        """
        raise NotImplementedError

    async def process(
        self,
        query: str,
        context: dict[str, Any] | None = None,
        intent: str | None = None,
        run_id: str | None = None,
        resume: bool = False,
    ) -> dict[str, Any]:
        """
        Process a user query through the orchestration pipeline.

        Args:
            query: User query text
            context: Optional context (history, metadata, etc.)
            intent: Optional forced intent string
            run_id: Optional stable id for durable checkpointing. Required to
                ``resume`` a prior run; auto-generated for a fresh run when a
                ``checkpoint_store`` is configured.
            resume: When True (with ``run_id`` and a configured store), reload
                the prior checkpoint and continue — completed tool steps replay
                from the store instead of re-executing.

        Returns:
            Processing result dictionary
        """
        from core.orchestration.budget_context import (
            activate_budget,
            deactivate_budget,
        )
        from core.orchestration.guard_pipeline import guard_input, guard_output

        # Input guardrails run before any budget/LLM spend; a blocked query
        # returns a structured result instead of entering the loop.
        blocked = guard_input(query)
        if blocked is not None:
            return blocked

        context = context or {}

        # Inject per-request budget tracker. Handlers downstream call
        # ``budget.tick()`` before each agent loop step; LLM dollar cost is
        # charged ambiently via ``budget_context`` from inside LLMService, so
        # every generate call in this request counts against ``budget_usd``.
        budget = LoopBudget(limits=getattr(self, "loop_limits", LoopLimits()))
        context["loop_budget"] = budget
        token = activate_budget(budget)
        try:
            result = await self._process_with_budget(
                query, context, intent, budget, run_id, resume
            )
            # Output guardrails (PII redaction, harmful-content filter) on the
            # final response text before it leaves the orchestrator.
            return guard_output(result)
        finally:
            deactivate_budget(token)

    async def _init_checkpoint(
        self,
        store: "CheckpointStore",
        query: str,
        context: dict[str, Any],
        intent: str | None,
        budget: LoopBudget,
        run_id: str | None,
        resume: bool,
    ) -> "CheckpointManager":
        """Create or resume a checkpoint (body in ``checkpoint.init_checkpoint``)."""
        from core.orchestration.checkpoint import init_checkpoint

        return await init_checkpoint(
            store, query, context, intent, budget, run_id, resume
        )

    async def _process_with_budget(
        self,
        query: str,
        context: dict[str, Any],
        intent: str | None,
        budget: LoopBudget,
        run_id: str | None = None,
        resume: bool = False,
    ) -> dict[str, Any]:
        """Body of :meth:`process`, run with the budget bound as ambient."""
        if getattr(self, "contract_validator", None) is not None:
            context["contract_validator"] = self.contract_validator
        if getattr(self, "autonomy_policy", None) is not None:
            context["autonomy_policy"] = self.autonomy_policy

        # 0. Tenant isolation guard — prevent cross-tenant leakage if middleware
        #    has been bypassed or context has been tampered. The middleware sets
        #    the ambient tenant; here we ensure context agrees.
        enforce_tenant_isolation(context)

        # 0b. Durable checkpoint setup. When a store is configured, create or
        #     resume a checkpoint and expose the manager on the context so
        #     handlers can make their tool steps idempotent (context["checkpoint"]).
        checkpoint_mgr: CheckpointManager | None = None
        store = getattr(self, "checkpoint_store", None)
        if store is not None:
            checkpoint_mgr = await self._init_checkpoint(
                store, query, context, intent, budget, run_id, resume
            )
            context["checkpoint"] = checkpoint_mgr
            # Resume restores the previously-classified intent, skipping a
            # redundant (and possibly nondeterministic) re-classification.
            if checkpoint_mgr.checkpoint.intent:
                intent = checkpoint_mgr.checkpoint.intent

        # 1. Retrieve Context from Memory + classify intent.
        #    Memory recall (embedding + vector search) and intent classification
        #    (which consumes only `query`) are independent, so overlap them
        #    instead of paying both latencies in series. When intent is already
        #    known (provided or restored from a checkpoint), recall runs alone.
        if not intent:
            _, intent = await asyncio.gather(
                inject_memory_context(self, query, context, budget),
                self.classify_intent_async(query),
            )
        else:
            await inject_memory_context(self, query, context, budget)

        # Intent is always resolved by here (provided, restored, or classified);
        # the assert restores the concrete type after gather's Any unpacking.
        assert intent is not None

        # 2. Inject Capabilities (human intervention, feedback, skills catalog).
        inject_capabilities(self, context)

        context["intent"] = intent

        # Record the freshly-classified intent on a new checkpoint so a resume
        # of this run reuses it instead of re-classifying.
        if checkpoint_mgr is not None and checkpoint_mgr.checkpoint.intent is None:
            checkpoint_mgr.checkpoint.intent = intent

        start_time = time.time()

        # Structured run-event stream (astream_events equivalent): lifecycle
        # events flow whenever the run is addressable by id — with or without
        # a checkpoint store. No subscribers ⇒ publish is a no-op.
        events_run_id = checkpoint_mgr.run_id if checkpoint_mgr is not None else run_id
        publish_run_event(events_run_id, EventType.RUN_STARTED, {"intent": intent})

        # Emit flow started event
        if _HAS_EVENT_BUS:
            try:
                get_event_bus().emit_sync(
                    EventNames.FLOW_STARTED,
                    {"intent": intent, "query": query[:100]},
                )
            except Exception as e:
                logger.warning(f"Failed to emit start event: {e}")

        # Get handler for intent
        handler = self._flow_handlers.get(intent)

        if not handler:
            logger.warning(f"No handler registered for intent: {intent}")
            return {
                "response": f"No handler available for intent: {intent}",
                "intent": intent,
                "error": True,
            }

        # Execute handler — bound to its owning plugin so downstream seams
        # (e.g. the central per-plugin LLM policy) attribute the work to it.
        try:
            plugin_token = self._bind_intent_plugin(intent)
            try:
                result = await handler.handle(query, context)
            finally:
                if plugin_token is not None:
                    reset_plugin_context(plugin_token)
            result["intent"] = intent
            result["budget"] = budget.snapshot().__dict__

            # 3. Save Interaction to Memory — in the background. Each write
            #    is an embedding + vector upsert; awaiting them here adds two
            #    round trips of latency AFTER the answer is already computed.
            if self.memory_manager:
                self._schedule_memory_write(query, result.get("response", ""), intent)

            # Emit flow completed event
            if _HAS_EVENT_BUS:
                elapsed = time.time() - start_time

                # Create safe context for event (exclude complex objects)
                safe_context = {
                    k: v
                    for k, v in context.items()
                    if isinstance(v, (str, int, float, bool, list, dict))
                    and k != "memory_manager"
                }

                try:
                    get_event_bus().emit_sync(
                        EventNames.FLOW_COMPLETED,
                        {
                            "intent": intent,
                            "query": query,
                            "response": result.get("response", ""),
                            "context": safe_context,
                            "duration_ms": int(elapsed * 1000),
                            "success": not result.get("error", False),
                        },
                    )
                except Exception as e:
                    logger.warning(f"Failed to emit completion event: {e}")

            # Persist final budget + mark the checkpoint completed.
            if checkpoint_mgr is not None:
                checkpoint_mgr.update_budget(budget.snapshot())
                await checkpoint_mgr.complete(result.get("response"))

            publish_run_event(
                events_run_id,
                EventType.RESPONSE_FINAL,
                {"intent": intent, "response": result.get("response", "")},
            )
            return result
        except ApprovalPendingError as e:
            # Durable human-in-the-loop pause: the checkpoint is already
            # persisted as awaiting_approval (with the pending tool/category);
            # persist the budget too so the resumed run keeps its caps. Not an
            # error — the caller records a decision and resumes by run_id.
            logger.info(
                "run_paused_awaiting_approval",
                extra={"intent": intent, "run_id": e.run_id, "tool": e.tool_name},
            )
            if checkpoint_mgr is not None:
                checkpoint_mgr.update_budget(budget.snapshot())
                await checkpoint_mgr.store.save(checkpoint_mgr.checkpoint)
            publish_run_event(
                events_run_id,
                EventType.HUMAN_REQUEST,
                {"tool_name": e.tool_name, "category": e.category},
            )
            return {
                "response": str(e),
                "intent": intent,
                "error": False,
                "awaiting_approval": True,
                "run_id": e.run_id,
                "pending_approval": {
                    "tool_name": e.tool_name,
                    "category": e.category,
                },
            }
        except BudgetExceededError as e:
            logger.warning(
                "loop_budget_exceeded",
                extra={
                    "intent": intent,
                    "reason": e.reason,
                    "snapshot": str(e.snapshot),
                },
            )
            if checkpoint_mgr is not None:
                await checkpoint_mgr.fail(f"budget_exceeded: {e.reason}")
            publish_run_event(
                events_run_id,
                EventType.ERROR,
                {"error": f"budget_exceeded: {e.reason}"},
            )
            return {
                "response": f"Request aborted: {e.reason}",
                "intent": intent,
                "error": True,
                "budget_exceeded": e.reason,
                "budget": e.snapshot.__dict__,
            }
        except Exception as e:
            logger.error(f"Handler error for intent {intent}: {e}")
            # Mark failed but keep the checkpoint — a resumable run survives the
            # crash and completed steps replay instead of re-executing.
            if checkpoint_mgr is not None:
                try:
                    await checkpoint_mgr.fail(str(e))
                except Exception as cp_err:
                    logger.warning(f"Failed to persist checkpoint failure: {cp_err}")

            # Emit flow failed event
            if _HAS_EVENT_BUS:
                elapsed = time.time() - start_time
                try:
                    get_event_bus().emit_sync(
                        EventNames.FLOW_COMPLETED,
                        {
                            "intent": intent,
                            "duration_ms": int(elapsed * 1000),
                            "success": False,
                            "error": str(e),
                        },
                    )
                except Exception as e_emit:
                    logger.warning(f"Failed to emit failure event: {e_emit}")

            publish_run_event(events_run_id, EventType.ERROR, {"error": str(e)})
            return {
                "response": f"Error processing request: {e!s}",
                "intent": intent,
                "error": True,
            }

    async def process_stream(
        self,
        query: str,
        context: dict[str, Any] | None = None,
        intent: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Process a user query with streaming response.

        Args:
            query: User query text
            context: Optional context
            intent: Optional forced intent string

        Yields:
            Response tokens/chunks
        """
        from core.orchestration.guard_pipeline import guard_input

        # Input guardrails run before any classification/LLM spend, same as
        # the non-streaming path. Note: OutputGuard is not applied to streamed
        # chunks — redaction patterns can't match content split across chunk
        # boundaries; use the non-streaming path when output filtering is
        # required.
        blocked = guard_input(query)
        if blocked is not None:
            yield blocked["response"]
            return

        context = context or {}

        # Classify intent if not provided
        if not intent:
            intent = await self.classify_intent_async(query)
        context["intent"] = intent

        # Get stream handler for intent
        handler = self._stream_handlers.get(intent)

        if not handler:
            # Fall back to non-streaming if no stream handler
            logger.debug(f"No stream handler for intent: {intent}, using sync fallback")
            yield f"[INFO] Processing {intent}..."
            return

        # Execute streaming handler — bound to its owning plugin (LLM policy).
        try:
            plugin_token = self._bind_intent_plugin(intent)
            try:
                async for chunk in handler.handle(query, context):
                    yield chunk
            finally:
                if plugin_token is not None:
                    reset_plugin_context(plugin_token)
        except Exception as e:
            logger.error(f"Stream handler error for intent {intent}: {e}")
            yield f"[ERROR] {e!s}"
