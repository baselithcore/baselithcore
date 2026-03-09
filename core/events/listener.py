"""
Event Listener for System Observability.

Provides centralized event handling for logging, metrics, and monitoring.
Attaches to the EventBus to observe all system events.

Usage:
    from core.events import get_event_bus
    from core.events.listener import EventListener

    # Start observability
    listener = EventListener.setup()

    # Events are now automatically logged and tracked
    # Access metrics via listener.get_metrics()
"""

from core.observability.logging import get_logger
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.events.bus import EventBus, get_event_bus
from core.events.names import EventNames

logger = get_logger(__name__)


@dataclass
class EventMetrics:
    """Aggregated metrics from observed events."""

    flow_count: int = 0
    flow_success: int = 0
    flow_failed: int = 0
    total_duration_ms: int = 0
    experiences_recorded: int = 0
    total_rewards: float = 0.0
    agents_started: int = 0
    agents_completed: int = 0
    agents_failed: int = 0
    plugins_loaded: int = 0

    # Per-intent tracking
    intent_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    intent_durations: Dict[str, List[int]] = field(
        default_factory=lambda: defaultdict(list)
    )

    @property
    def avg_flow_duration_ms(self) -> float:
        """Average flow duration in milliseconds."""
        if self.flow_count == 0:
            return 0.0
        return self.total_duration_ms / self.flow_count

    @property
    def success_rate(self) -> float:
        """Flow success rate as percentage."""
        if self.flow_count == 0:
            return 0.0
        return (self.flow_success / self.flow_count) * 100

    @property
    def avg_reward(self) -> float:
        """Average reward per experience."""
        if self.experiences_recorded == 0:
            return 0.0
        return self.total_rewards / self.experiences_recorded

    def get_intent_stats(self) -> Dict[str, Any]:
        """Get per-intent statistics."""
        stats = {}
        for intent, count in self.intent_counts.items():
            durations = self.intent_durations.get(intent, [])
            stats[intent] = {
                "count": count,
                "avg_duration_ms": sum(durations) / len(durations) if durations else 0,
                "min_duration_ms": min(durations) if durations else 0,
                "max_duration_ms": max(durations) if durations else 0,
            }
        return stats

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "flows": {
                "total": self.flow_count,
                "success": self.flow_success,
                "failed": self.flow_failed,
                "success_rate": f"{self.success_rate:.1f}%",
                "avg_duration_ms": int(self.avg_flow_duration_ms),
            },
            "learning": {
                "experiences": self.experiences_recorded,
                "total_rewards": self.total_rewards,
                "avg_reward": self.avg_reward,
            },
            "agents": {
                "started": self.agents_started,
                "completed": self.agents_completed,
                "failed": self.agents_failed,
            },
            "plugins": {
                "loaded": self.plugins_loaded,
            },
            "intents": self.get_intent_stats(),
        }


class EventListener:
    """
    Centralized event listener for system observability.

    Attaches handlers to the EventBus to:
    - Log all significant events
    - Collect metrics for monitoring
    - Track performance statistics

    Example:
        ```python
        # Setup listener (typically at app startup)
        listener = EventListener.setup()

        # Later, get metrics
        metrics = listener.get_metrics()
        print(f"Success rate: {metrics['flows']['success_rate']}")

        # Get recent events
        history = listener.get_recent_events(limit=10)
        ```
    """

    _instance: Optional["EventListener"] = None

    def __init__(self, bus: Optional[EventBus] = None):
        """
        Initialize event listener.

        Args:
            bus: EventBus to listen on (uses global if None)
        """
        self.bus = bus or get_event_bus()
        self.metrics = EventMetrics()
        self._recent_events: List[Dict[str, Any]] = []
        self._max_recent = 100
        self._attached = False

    @classmethod
    def setup(cls, bus: Optional[EventBus] = None) -> "EventListener":
        """
        Setup and return singleton listener.

        Args:
            bus: EventBus to listen on

        Returns:
            EventListener instance
        """
        if cls._instance is None:
            cls._instance = cls(bus)
            cls._instance.attach()
        return cls._instance

    @classmethod
    def get_instance(cls) -> Optional["EventListener"]:
        """Get existing listener instance."""
        return cls._instance

    def attach(self) -> None:
        """Attach all event handlers to the bus."""
        if self._attached:
            return

        # Flow events
        self.bus.subscribe(EventNames.FLOW_STARTED, self._on_flow_started)
        self.bus.subscribe(EventNames.FLOW_COMPLETED, self._on_flow_completed)

        # Learning events
        self.bus.subscribe(EventNames.EXPERIENCE_RECORDED, self._on_experience_recorded)
        self.bus.subscribe(EventNames.LEARNING_UPDATED, self._on_learning_updated)

        # Agent events
        self.bus.subscribe(EventNames.AGENT_STARTED, self._on_agent_started)
        self.bus.subscribe(EventNames.AGENT_COMPLETED, self._on_agent_completed)
        self.bus.subscribe(EventNames.AGENT_FAILED, self._on_agent_failed)

        # Plugin events
        self.bus.subscribe(EventNames.PLUGIN_LOADED, self._on_plugin_loaded)

        # System events
        self.bus.subscribe(EventNames.SYSTEM_READY, self._on_system_ready)
        self.bus.subscribe(EventNames.SYSTEM_SHUTDOWN, self._on_system_shutdown)

        self._attached = True
        logger.info("EventListener attached to EventBus")

    def _log_event(self, event_name: str, data: Dict[str, Any]) -> None:
        """Log event and add to recent list."""
        entry = {
            "event": event_name,
            "data": data,
            "timestamp": time.time(),
        }
        self._recent_events.append(entry)
        if len(self._recent_events) > self._max_recent:
            self._recent_events = self._recent_events[-self._max_recent :]

    # Flow handlers
    def _on_flow_started(self, data: Dict[str, Any]) -> None:
        """Handle flow started event."""
        intent = data.get("intent", "unknown")
        query = data.get("query", "")[:50]
        logger.debug(f"Flow started: {intent} - '{query}...'")
        self._log_event(EventNames.FLOW_STARTED, data)

    def _on_flow_completed(self, data: Dict[str, Any]) -> None:
        """Handle flow completed event."""
        intent = data.get("intent", "unknown")
        duration = data.get("duration_ms", 0)
        success = data.get("success", True)

        self.metrics.flow_count += 1
        self.metrics.total_duration_ms += duration
        self.metrics.intent_counts[intent] += 1
        self.metrics.intent_durations[intent].append(duration)

        if success:
            self.metrics.flow_success += 1
            logger.debug(f"Flow completed: {intent} in {duration}ms")
        else:
            self.metrics.flow_failed += 1
            error = data.get("error", "unknown error")
            logger.warning(f"Flow failed: {intent} - {error}")

        self._log_event(EventNames.FLOW_COMPLETED, data)

    # Learning handlers
    def _on_experience_recorded(self, data: Dict[str, Any]) -> None:
        """Handle experience recorded event."""
        action = data.get("action", "unknown")
        reward = data.get("reward", 0.0)

        self.metrics.experiences_recorded += 1
        self.metrics.total_rewards += reward

        logger.debug(f"Experience recorded: {action} (reward={reward:.2f})")
        self._log_event(EventNames.EXPERIENCE_RECORDED, data)

    def _on_learning_updated(self, data: Dict[str, Any]) -> None:
        """Handle learning updated event."""
        logger.info(f"Learning updated: {data}")
        self._log_event(EventNames.LEARNING_UPDATED, data)

    # Agent handlers
    def _on_agent_started(self, data: Dict[str, Any]) -> None:
        """Handle agent started event."""
        self.metrics.agents_started += 1
        agent_id = data.get("agent_id", "unknown")
        logger.debug(f"Agent started: {agent_id}")
        self._log_event(EventNames.AGENT_STARTED, data)

    def _on_agent_completed(self, data: Dict[str, Any]) -> None:
        """Handle agent completed event."""
        self.metrics.agents_completed += 1
        agent_id = data.get("agent_id", "unknown")
        logger.debug(f"Agent completed: {agent_id}")
        self._log_event(EventNames.AGENT_COMPLETED, data)

    def _on_agent_failed(self, data: Dict[str, Any]) -> None:
        """Handle agent failed event."""
        self.metrics.agents_failed += 1
        agent_id = data.get("agent_id", "unknown")
        error = data.get("error", "unknown error")
        logger.warning(f"Agent failed: {agent_id} - {error}")
        self._log_event(EventNames.AGENT_FAILED, data)

    # Plugin handlers
    def _on_plugin_loaded(self, data: Dict[str, Any]) -> None:
        """Handle plugin loaded event."""
        self.metrics.plugins_loaded += 1
        name = data.get("name", "unknown")
        action = data.get("action", "load")
        logger.info(f"Plugin {action}: {name}")
        self._log_event(EventNames.PLUGIN_LOADED, data)

    # System handlers
    def _on_system_ready(self, data: Dict[str, Any]) -> None:
        """Handle system ready event."""
        logger.info("System ready")
        self._log_event(EventNames.SYSTEM_READY, data)

    def _on_system_shutdown(self, data: Dict[str, Any]) -> None:
        """Handle system shutdown event."""
        logger.info("System shutdown initiated")
        self._log_event(EventNames.SYSTEM_SHUTDOWN, data)

    def get_metrics(self) -> Dict[str, Any]:
        """
        Get current metrics.

        Returns:
            Metrics dictionary
        """
        return self.metrics.to_dict()

    def get_recent_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent events.

        Args:
            limit: Maximum events to return

        Returns:
            List of recent events
        """
        return self._recent_events[-limit:]

    def reset_metrics(self) -> None:
        """Reset all metrics."""
        self.metrics = EventMetrics()
        self._recent_events.clear()
        logger.info("EventListener metrics reset")


__all__ = ["EventListener", "EventMetrics"]
