"""
Autonomous Performance Evaluation and Quality Benchmarking.

Provides a systematic engine for assessing agent and model quality.
Orchestrates 'LLM-as-a-Judge' patterns, executes golden-set tests,
and synthesizes multi-dimensional metrics (accuracy, safety, latency)
to provide continuous validation of system updates.
"""

import asyncio
from typing import Any, Dict, Optional

from core.events import EventBus, EventNames, get_event_bus
from .protocols import Evaluator, EvaluationResult
from .judges import CompositeEvaluator

from core.observability.logging import get_logger

logger = get_logger(__name__)


class EvaluationService:
    """
    Service for continuous evaluation of agent interactions.
    """

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        evaluator: Optional[Evaluator] = None,
    ):
        self.event_bus = event_bus or get_event_bus()
        self.evaluator = evaluator or CompositeEvaluator()
        self._running = False
        self._tasks: set[asyncio.Task] = set()

    def start(self):
        """Start listening to events."""
        if self._running:
            return

        from core.config.evaluation import evaluation_config

        if not evaluation_config.enabled:
            logger.info("EvaluationService disabled by config")
            return

        self.event_bus.subscribe(EventNames.FLOW_COMPLETED, self._on_flow_completed)
        self._running = True
        logger.info("EvaluationService started")

    def stop(self):
        """Stop listening to events."""
        # Note: EventBus doesn't support unsubscribe easily by method ref yet without storing the wrapper
        # For now, we just flip the flag
        self._running = False
        logger.info("EvaluationService stopped")

    async def _on_flow_completed(self, data: Dict[str, Any]):
        """Handle flow completion event."""
        if not self._running:
            return

        # Don't evaluate failed flows or internal evaluation flows
        if not data.get("success", False):
            return

        intent = data.get("intent")
        if not intent or intent.startswith("evaluation"):
            return

        # Extract context
        query = data.get("query", "")
        response = data.get(
            "response", ""
        )  # Assuming this is passed, orchestrator needs to pass it
        context = data.get("context", {})

        if not query or not response:
            # If query/response not in event data, we might need to fetch from memory?
            # For now, assuming orchestrator includes them in FLOW_COMPLETED event data
            # Logic in Orchestrator needs to be updated to include 'response' in event
            return

        # Create background task for evaluation
        task = asyncio.create_task(
            self._evaluate_interaction(query, response, context, intent)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _evaluate_interaction(
        self,
        query: str,
        response: str,
        context: Dict[str, Any],
        intent: str,
    ):
        """Run evaluation and emit result."""
        try:
            # Emit started event
            await self.event_bus.emit(EventNames.EVALUATION_STARTED, {"intent": intent})

            # Evaluate
            result: EvaluationResult = await self.evaluator.evaluate(
                response=response, query=query, context=context
            )

            # Emit completed event
            await self.event_bus.emit(
                EventNames.EVALUATION_COMPLETED,
                {
                    "intent": intent,
                    "score": result.score,
                    "quality": result.quality.value,
                    "feedback": result.feedback,
                    "aspects": result.aspects,
                    "should_refine": result.should_refine,
                    "metadata": result.metadata,
                },
            )

            logger.info(
                f"Evaluation complete for {intent}: {result.quality.value} ({result.score:.2f})"
            )

        except Exception as e:
            logger.error(f"Evaluation failed: {e}")
            await self.event_bus.emit(
                EventNames.EVALUATION_FAILED, {"intent": intent, "error": str(e)}
            )
