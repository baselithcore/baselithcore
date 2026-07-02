"""Execution Mixin for Orchestrator."""

import asyncio
from core.observability.logging import get_logger
import time
from typing import Any, AsyncGenerator, Dict, Optional, TYPE_CHECKING

from core.orchestration.limits import (
    BudgetExceededError,
    LoopBudget,
    LoopLimits,
)

try:
    from core.events import get_event_bus, EventNames

    _HAS_EVENT_BUS = True
except ImportError:
    _HAS_EVENT_BUS = False

if TYPE_CHECKING:
    from core.memory import AgentMemory
    from core.human import HumanIntervention
    from core.learning import FeedbackCollector
    from core.orchestration.autonomy import AutonomyPolicy
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
    _flow_handlers: Dict[str, "FlowHandler"]
    _stream_handlers: Dict[str, "StreamHandler"]
    # References to in-flight background memory writes: fire-and-forget tasks
    # without a reference can be garbage-collected mid-run.
    _memory_write_tasks: set

    def _schedule_memory_write(
        self, query: str, response_text: str, intent: Optional[str]
    ) -> None:
        """Persist the interaction to memory off the request path.

        Each remember() call costs an embedding pass plus a vector upsert;
        running them post-response in a tracked background task removes that
        latency from the caller without losing failures (logged via the done
        callback).
        """
        memory_manager = self.memory_manager
        if memory_manager is None:
            return
        if not hasattr(self, "_memory_write_tasks"):
            self._memory_write_tasks = set()

        async def _write() -> None:
            await memory_manager.remember(
                f"User Query: {query}",
                metadata={"type": "query", "intent": intent},
            )
            if response_text:
                await memory_manager.remember(
                    f"Agent Response: {response_text}",
                    metadata={"type": "response", "intent": intent},
                )

        task = asyncio.create_task(_write())
        self._memory_write_tasks.add(task)

        def _done(finished: asyncio.Task) -> None:
            self._memory_write_tasks.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                logger.warning(f"Failed to save memory: {finished.exception()}")

        task.add_done_callback(_done)

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
        context: Optional[Dict[str, Any]] = None,
        intent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process a user query through the orchestration pipeline.

        Args:
            query: User query text
            context: Optional context (history, metadata, etc.)
            intent: Optional forced intent string

        Returns:
            Processing result dictionary
        """
        from core.orchestration.budget_context import (
            activate_budget,
            deactivate_budget,
        )

        context = context or {}

        # Inject per-request budget tracker. Handlers downstream call
        # ``budget.tick()`` before each agent loop step; LLM dollar cost is
        # charged ambiently via ``budget_context`` from inside LLMService, so
        # every generate call in this request counts against ``budget_usd``.
        budget = LoopBudget(limits=getattr(self, "loop_limits", LoopLimits()))
        context["loop_budget"] = budget
        token = activate_budget(budget)
        try:
            return await self._process_with_budget(query, context, intent, budget)
        finally:
            deactivate_budget(token)

    async def _process_with_budget(
        self,
        query: str,
        context: Dict[str, Any],
        intent: Optional[str],
        budget: LoopBudget,
    ) -> Dict[str, Any]:
        """Body of :meth:`process`, run with the budget bound as ambient."""
        if getattr(self, "contract_validator", None) is not None:
            context["contract_validator"] = self.contract_validator
        if getattr(self, "autonomy_policy", None) is not None:
            context["autonomy_policy"] = self.autonomy_policy

        # 0. Tenant isolation guard — prevent cross-tenant leakage if middleware
        #    has been bypassed or context has been tampered. The middleware sets
        #    the ambient tenant; here we ensure context agrees.
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

        # 1. Retrieve Context from Memory. Recall and history assembly are
        #    independent reads — overlap them instead of awaiting serially.
        if self.memory_manager:
            try:
                if hasattr(self.memory_manager, "get_context_async"):
                    memories, recent_history = await asyncio.gather(
                        self.memory_manager.recall(query, limit=5),
                        self.memory_manager.get_context_async(max_tokens=2000),
                    )
                else:
                    memories = await self.memory_manager.recall(query, limit=5)
                    recent_history = self.memory_manager.get_context(max_tokens=2000)

                # Flatten for prompt context
                memory_text = "\n".join([f"- {m.content}" for m in memories])
                context["memory_context"] = memory_text

                # FEATURE: Context Folding Integration
                # Inject recent conversation history (potentially folded)
                context["recent_history"] = recent_history

                # Also expose the manager itself to agents
                context["memory_manager"] = self.memory_manager
            except Exception as e:
                logger.warning(f"Memory recall failed: {e}")

        # 2. Inject Capabilities
        if self.human_intervention:
            context["human_intervention"] = self.human_intervention

        if self.feedback_collector:
            context["feedback_collector"] = self.feedback_collector

        # Classify intent if not provided
        if not intent:
            intent = await self.classify_intent_async(query)
        context["intent"] = intent

        start_time = time.time()

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

        # Execute handler
        try:
            result = await handler.handle(query, context)
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

            return result
        except BudgetExceededError as e:
            logger.warning(
                "loop_budget_exceeded",
                extra={
                    "intent": intent,
                    "reason": e.reason,
                    "snapshot": str(e.snapshot),
                },
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

            return {
                "response": f"Error processing request: {str(e)}",
                "intent": intent,
                "error": True,
            }

    async def process_stream(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        intent: Optional[str] = None,
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

        # Execute streaming handler
        try:
            async for chunk in handler.handle(query, context):
                yield chunk
        except Exception as e:
            logger.error(f"Stream handler error for intent {intent}: {e}")
            yield f"[ERROR] {str(e)}"
