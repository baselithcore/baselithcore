"""
Evolution Service.

Handles agent self-improvement by monitoring performance and triggering
automatic fine-tuning and memory refinement.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Any, Optional, TYPE_CHECKING

from core.events import get_event_bus, EventNames
from core.memory import AgentMemory
from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.learning.auto_finetuning import AutoFineTuningService

logger = get_logger(__name__)


class EvolutionService:
    """
    Service for agent self-evolution.

    Monitors evaluation results and manages agent updates,
    fine-tuning, and memory pruning.
    """

    def __init__(
        self,
        memory_manager: Optional[AgentMemory] = None,
        enable_auto_finetuning: bool = True,
    ):
        self.event_bus = get_event_bus()
        self.memory_manager = memory_manager
        self._running = False
        self._tasks: set[asyncio.Task] = set()
        self._enable_auto_finetuning = enable_auto_finetuning
        self._auto_ft_service: Optional["AutoFineTuningService"] = None

    @property
    def auto_finetuning_service(self) -> Optional["AutoFineTuningService"]:
        """Lazy load AutoFineTuningService."""
        if self._auto_ft_service is None and self._enable_auto_finetuning:
            try:
                from core.learning.auto_finetuning import AutoFineTuningService

                self._auto_ft_service = AutoFineTuningService()
            except Exception as e:
                logger.error(f"Failed to load AutoFineTuningService: {e}")
        return self._auto_ft_service

    def start(self) -> None:
        """Start evolution monitoring."""
        if self._running:
            return

        self._running = True
        self.event_bus.subscribe(
            EventNames.EVALUATION_COMPLETED, self._on_evaluation_completed
        )

        # Start auto fine-tuning service if enabled
        if self.auto_finetuning_service:
            self.auto_finetuning_service.start()

        logger.info("EvolutionService started")

    def stop(self) -> None:
        """Stop evolution monitoring."""
        self._running = False
        if self._auto_ft_service:
            self._auto_ft_service.stop()
        logger.info("EvolutionService stopped")

    async def _on_evaluation_completed(self, data: Dict[str, Any]) -> None:
        """Process evaluation results."""
        if not self._running:
            return

        score = data.get("score", 0.0)
        logger.debug(f"EvolutionService processing score: {score}")

        # Update memory based on performance if memory manager exists
        if self.memory_manager:
            if score < 0.4:
                # Store Lesson Learned
                await self.memory_manager.remember(
                    content=f"Lesson Learned from {data.get('intent', 'unknown_intent')}: {data.get('feedback', '')}",
                    metadata={
                        "title": "Lesson Learned",
                        "score": score,
                        "type": "lesson_learned",
                        "intent": data.get("intent", ""),
                    },
                )
            elif score > 0.9:
                # Store Best Practice
                await self.memory_manager.remember(
                    content=f"Best Practice for {data.get('intent', 'unknown_intent')}: {data.get('response', '')[:200]}...",
                    metadata={
                        "title": "Best Practice",
                        "score": score,
                        "type": "best_practice",
                        "intent": data.get("intent", ""),
                    },
                )

    def get_evolution_stats(self) -> Dict[str, Any]:
        """Get evolution metrics."""
        stats: Dict[str, Any] = {
            "running": self._running,
            "auto_ft_enabled": self._enable_auto_finetuning,
        }

        if self._auto_ft_service:
            stats["auto_finetuning"] = self._auto_ft_service.get_stats()

        return stats

    async def trigger_manual_finetuning(self) -> Optional[str]:
        """Manually trigger fine-tuning through the service."""
        if self.auto_finetuning_service:
            return await self.auto_finetuning_service.trigger_finetuning()
        logger.warning("AutoFineTuningService not available")
        return None
