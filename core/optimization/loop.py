"""
Optimization Loop

Event-driven bridge between the evaluation system and the
:class:`PromptOptimizer`.  Subscribes to ``EVALUATION_COMPLETED`` events
and triggers ``auto_tune()`` for agents whose score drops below a
configurable threshold.
"""

from __future__ import annotations

import asyncio
from core.observability.logging import get_logger
from typing import Any, Dict, Optional

from core.events import EventBus, EventNames, get_event_bus
from core.learning.feedback import FeedbackCollector
from core.optimization.optimizer import PromptOptimizer, _ApplyFn

logger = get_logger(__name__)


class OptimizationLoop:
    """React to evaluation results and optimise under-performing agents.

    Typical bootstrap::

        loop = OptimizationLoop(
            feedback_collector=collector,
            apply_fn=my_apply_callback,
        )
        loop.start()          # subscribes to EventBus
        # ... later ...
        loop.stop()

    The loop only fires ``auto_tune`` when:

    * The evaluation score is below *threshold*.
    * The event payload contains an ``agent_id`` (or it is inferred from
      the *intent* field as a fallback).
    * No other tune is already running for the same agent (prevents
      concurrent optimisation storms).
    """

    def __init__(
        self,
        feedback_collector: Optional[FeedbackCollector] = None,
        apply_fn: Optional[_ApplyFn] = None,
        *,
        threshold: float = 0.5,
        dry_run: bool = False,
        event_bus: Optional[EventBus] = None,
    ):
        self._event_bus = event_bus or get_event_bus()
        self._collector = feedback_collector or FeedbackCollector()
        self._optimizer = PromptOptimizer(self._collector)
        self._apply_fn = apply_fn
        self._threshold = threshold
        self._dry_run = dry_run
        self._running = False
        self._in_flight: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._unsubscribe: Optional[Any] = None

    def start(self) -> None:
        """Subscribe to evaluation events on the EventBus."""
        if self._running:
            return
        self._unsubscribe = self._event_bus.subscribe(
            EventNames.EVALUATION_COMPLETED, self._on_evaluation_completed
        )
        self._running = True
        logger.info(
            "OptimizationLoop started (threshold=%.2f, dry_run=%s)",
            self._threshold,
            self._dry_run,
        )

    def stop(self) -> None:
        """Unsubscribe from events and stop the loop."""
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        self._running = False
        logger.info("OptimizationLoop stopped")

    async def _on_evaluation_completed(self, data: Dict[str, Any]) -> None:
        if not self._running:
            return

        score = data.get("score", 1.0)
        if score >= self._threshold:
            return

        agent_id = data.get("agent_id") or data.get("intent", "")
        if not agent_id:
            return

        # Prevent concurrent tuning for the same agent
        if agent_id in self._in_flight:
            return

        self._in_flight.add(agent_id)
        task = asyncio.create_task(self._tune_agent(agent_id, score))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _tune_agent(self, agent_id: str, score: float) -> None:
        try:
            logger.info(
                "Triggering auto_tune for '%s' (score=%.2f < %.2f)",
                agent_id,
                score,
                self._threshold,
            )
            result = await self._optimizer.auto_tune(
                agent_id=agent_id,
                apply_fn=self._apply_fn,
                dry_run=self._dry_run,
            )

            if result:
                await self._event_bus.emit(
                    EventNames.OPTIMIZATION_COMPLETED,
                    {
                        "agent_id": agent_id,
                        "suggestion": result.suggestion,
                        "applied": result.applied,
                        "previous_score": result.previous_score,
                    },
                )
        except Exception as exc:
            logger.error("auto_tune failed for '%s': %s", agent_id, exc)
        finally:
            self._in_flight.discard(agent_id)
