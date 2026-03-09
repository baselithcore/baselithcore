"""Execution Mixin for Orchestrator."""

from core.observability.logging import get_logger
import time
from typing import Any, AsyncGenerator, Dict, Optional, TYPE_CHECKING

try:
    from core.events import get_event_bus, EventNames

    _HAS_EVENT_BUS = True
except ImportError:
    _HAS_EVENT_BUS = False

if TYPE_CHECKING:
    from core.memory import AgentMemory
    from core.human import HumanIntervention
    from core.learning import FeedbackCollector
    from core.orchestration.protocols import FlowHandler, StreamHandler

logger = get_logger(__name__)


class ExecutionMixin:
    """Mixin for query execution and processing."""

    memory_manager: Optional["AgentMemory"]
    human_intervention: Optional["HumanIntervention"]
    feedback_collector: Optional["FeedbackCollector"]
    _flow_handlers: Dict[str, "FlowHandler"]
    _stream_handlers: Dict[str, "StreamHandler"]

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
        context = context or {}

        # 1. Retrieve Context from Memory
        if self.memory_manager:
            try:
                # Recall relevant memories
                memories = await self.memory_manager.recall(query, limit=5)
                # Flatten for prompt context
                memory_text = "\n".join([f"- {m.content}" for m in memories])
                context["memory_context"] = memory_text

                # FEATURE: Context Folding Integration
                # Inject recent conversation history (potentially folded)
                if hasattr(self.memory_manager, "get_context_async"):
                    context[
                        "recent_history"
                    ] = await self.memory_manager.get_context_async(max_tokens=2000)
                else:
                    context["recent_history"] = self.memory_manager.get_context(
                        max_tokens=2000
                    )

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

            # 3. Save Interaction to Memory
            if self.memory_manager:
                try:
                    # Save User Query
                    await self.memory_manager.remember(
                        f"User Query: {query}",
                        metadata={"type": "query", "intent": intent},
                    )
                    # Save Agent Response
                    response_text = result.get("response", "")
                    if response_text:
                        await self.memory_manager.remember(
                            f"Agent Response: {response_text}",
                            metadata={"type": "response", "intent": intent},
                        )
                except Exception as e:
                    logger.warning(f"Failed to save memory: {e}")

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
