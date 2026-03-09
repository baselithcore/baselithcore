"""
Unit Tests for Event Bus System.

Tests for EventBus, EventListener, and event-driven communication.
"""

import pytest
import asyncio
from unittest.mock import Mock, patch

from core.events import (
    EventBus,
    EventNames,
    get_event_bus,
    reset_event_bus,
)
from core.events.listener import EventListener, EventMetrics


# ============================================================================
# EventBus Tests
# ============================================================================


class TestEventBus:
    """Tests for EventBus."""

    def setup_method(self):
        """Reset global event bus before each test."""
        reset_event_bus()

    @patch("core.events.bus.get_events_config")
    def test_creation(self, mock_get_config):
        """Create event bus with defaults."""
        mock_config = Mock()
        mock_config.event_max_history = 100
        mock_config.event_enable_wildcards = True
        mock_config.event_enable_validation = False
        mock_config.event_enable_dlq = False
        mock_get_config.return_value = mock_config

        bus = EventBus()
        assert bus is not None
        assert bus.stats["events_published"] == 0

    def test_subscribe_and_emit_sync(self):
        """Subscribe and emit synchronously."""
        bus = EventBus()
        received = []

        def handler(data):
            received.append(data)

        bus.subscribe("test.event", handler)
        bus.emit_sync("test.event", {"key": "value"})

        assert len(received) == 1
        assert received[0]["key"] == "value"

    @pytest.mark.asyncio
    async def test_subscribe_and_emit_async(self):
        """Subscribe and emit asynchronously."""
        bus = EventBus()
        received = []

        async def handler(data):
            received.append(data)

        bus.subscribe("test.event", handler)
        count = await bus.emit("test.event", {"async": True})

        assert count == 1
        assert len(received) == 1
        assert received[0]["async"] is True

    def test_decorator_subscription(self):
        """Subscribe using decorator."""
        bus = EventBus()
        received = []

        @bus.on("decorated.event")
        def handler(data):
            received.append(data)

        bus.emit_sync("decorated.event", {"decorated": True})

        assert len(received) == 1

    def test_wildcard_subscription(self):
        """Wildcard event matching."""
        bus = EventBus()
        received = []

        def handler(data):
            received.append(data)

        bus.subscribe("agent.*", handler)

        bus.emit_sync("agent.started", {"id": "1"})
        bus.emit_sync("agent.completed", {"id": "2"})
        bus.emit_sync("other.event", {"id": "3"})

        assert len(received) == 2
        assert received[0]["id"] == "1"
        assert received[1]["id"] == "2"

    def test_priority_ordering(self):
        """Handler priority ordering."""
        bus = EventBus()
        order = []

        def handler_low(data):
            order.append("low")

        def handler_high(data):
            order.append("high")

        bus.subscribe("priority.test", handler_low, priority=0)
        bus.subscribe("priority.test", handler_high, priority=10)

        bus.emit_sync("priority.test", {})

        assert order == ["high", "low"]

    def test_unsubscribe(self):
        """Unsubscribe from events."""
        bus = EventBus()
        received = []

        def handler(data):
            received.append(data)

        unsubscribe = bus.subscribe("unsub.test", handler)

        bus.emit_sync("unsub.test", {"first": True})
        unsubscribe()
        bus.emit_sync("unsub.test", {"second": True})

        assert len(received) == 1
        assert received[0]["first"] is True

    def test_event_history(self):
        """Event history tracking."""
        bus = EventBus(max_history=5)

        for i in range(10):
            bus.emit_sync(f"history.{i}", {"index": i})

        history = bus.get_history(limit=10)
        assert len(history) == 5  # Max history is 5

    def test_stats_tracking(self):
        """Stats are updated correctly."""
        bus = EventBus()

        def handler(data):
            pass

        bus.subscribe("stats.test", handler)
        bus.emit_sync("stats.test", {})
        bus.emit_sync("stats.test", {})

        stats = bus.stats
        assert stats["events_published"] == 2
        assert stats["handlers_registered"] >= 1

    def test_no_handlers(self):
        """Emit with no handlers."""
        bus = EventBus()
        count = bus.emit_sync("no.handlers", {})
        assert count == 0

    def test_clear_handlers(self):
        """Clear all handlers."""
        bus = EventBus()
        received = []

        bus.subscribe("clear.test", lambda d: received.append(d))
        bus.clear_handlers()
        bus.emit_sync("clear.test", {})

        assert len(received) == 0

    def test_wildcard_matching_patterns(self):
        """Test various wildcard patterns."""
        bus = EventBus()
        assert bus._match_wildcard("*", "any.event") is True
        assert bus._match_wildcard("agent.*", "agent.started") is True
        assert bus._match_wildcard("agent.*", "other.event") is False
        assert bus._match_wildcard("*.completed", "agent.completed") is True
        assert bus._match_wildcard("*.completed", "agent.started") is False
        assert bus._match_wildcard("direct", "direct") is True

    @pytest.mark.asyncio
    async def test_emit_no_wait(self):
        """Emit without waiting for handlers."""
        bus = EventBus()
        received = asyncio.Event()

        async def handler(data):
            await asyncio.sleep(0.01)
            received.set()

        bus.subscribe("async.test", handler)
        # Should return immediately
        count = await bus.emit("async.test", {}, wait=False)
        assert count == 1
        assert not received.is_set()

        # Wait for it to finish eventually
        await asyncio.wait_for(received.wait(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_handler_error_dlq(self):
        """Error handling with Dead Letter Queue."""
        from core.events.validation import DeadLetterQueue

        dlq = DeadLetterQueue(max_size=10)
        bus = EventBus(enable_dlq=True, dlq=dlq)

        def failing_handler(data):
            raise ValueError("Boom")

        async def failing_async_handler(data):
            raise RuntimeError("Async Boom")

        bus.subscribe("fail.sync", failing_handler)
        bus.subscribe("fail.async", failing_async_handler)

        await bus.emit("fail.sync", {"id": 1})
        await bus.emit("fail.async", {"id": 2})

        assert bus.stats["errors"] == 2
        assert len(dlq.get_all()) == 2

        failures = dlq.get_all()
        assert any("Boom" in f.error for f in failures)
        assert any("Async Boom" in f.error for f in failures)

    def test_event_bus_repr(self):
        """Test string representation."""
        bus = EventBus()
        bus.subscribe("test", lambda d: None)
        assert "handlers=1" in repr(bus)


class TestEventNames:
    """Tests for predefined event names."""

    def test_standard_events_exist(self):
        """All standard events are defined."""
        assert EventNames.AGENT_STARTED == "agent.started"
        assert EventNames.AGENT_COMPLETED == "agent.completed"
        assert EventNames.FLOW_STARTED == "flow.started"
        assert EventNames.FLOW_COMPLETED == "flow.completed"
        assert EventNames.EXPERIENCE_RECORDED == "learning.experience_recorded"
        assert EventNames.PLUGIN_LOADED == "plugin.loaded"


class TestGlobalEventBus:
    """Tests for global event bus singleton."""

    def setup_method(self):
        """Reset global event bus before each test."""
        reset_event_bus()

    def test_get_event_bus_singleton(self):
        """Global bus is singleton."""
        bus1 = get_event_bus()
        bus2 = get_event_bus()
        assert bus1 is bus2

    def test_reset_event_bus(self):
        """Reset creates new instance."""
        bus1 = get_event_bus()
        reset_event_bus()
        bus2 = get_event_bus()
        assert bus1 is not bus2


# ============================================================================
# EventMetrics Tests
# ============================================================================


class TestEventMetrics:
    """Tests for EventMetrics."""

    def test_default_values(self):
        """Default metric values."""
        metrics = EventMetrics()
        assert metrics.flow_count == 0
        assert metrics.success_rate == 0.0
        assert metrics.avg_flow_duration_ms == 0.0

    def test_success_rate_calculation(self):
        """Success rate is calculated correctly."""
        metrics = EventMetrics()
        metrics.flow_count = 10
        metrics.flow_success = 8
        metrics.flow_failed = 2

        assert metrics.success_rate == 80.0

    def test_avg_duration_calculation(self):
        """Average duration is calculated correctly."""
        metrics = EventMetrics()
        metrics.flow_count = 4
        metrics.total_duration_ms = 1000

        assert metrics.avg_flow_duration_ms == 250.0

    def test_avg_reward_calculation(self):
        """Average reward is calculated correctly."""
        metrics = EventMetrics()
        metrics.experiences_recorded = 5
        metrics.total_rewards = 2.5

        assert metrics.avg_reward == 0.5

    def test_to_dict(self):
        """Convert to dictionary."""
        metrics = EventMetrics()
        metrics.flow_count = 10
        metrics.flow_success = 9

        d = metrics.to_dict()
        assert "flows" in d
        assert d["flows"]["total"] == 10
        assert d["flows"]["success"] == 9


# ============================================================================
# EventListener Tests
# ============================================================================


class TestEventListener:
    """Tests for EventListener."""

    def setup_method(self):
        """Reset state before each test."""
        reset_event_bus()
        EventListener._instance = None

    def test_setup_singleton(self):
        """Setup returns singleton."""
        listener1 = EventListener.setup()
        listener2 = EventListener.setup()
        assert listener1 is listener2

    def test_attach_handlers(self):
        """Handlers are attached to bus."""
        EventListener.setup()
        bus = get_event_bus()

        # Check some handlers are registered
        assert bus.stats["handlers_registered"] > 0

    def test_flow_metrics_tracking(self):
        """Flow events update metrics."""
        listener = EventListener.setup()
        bus = get_event_bus()

        bus.emit_sync(EventNames.FLOW_STARTED, {"intent": "test", "query": "hello"})
        bus.emit_sync(
            EventNames.FLOW_COMPLETED,
            {"intent": "test", "duration_ms": 100, "success": True},
        )
        bus.emit_sync(
            EventNames.FLOW_COMPLETED,
            {"intent": "test", "duration_ms": 200, "success": False},
        )

        metrics = listener.get_metrics()
        assert metrics["flows"]["total"] == 2
        assert metrics["flows"]["success"] == 1
        assert metrics["flows"]["failed"] == 1

    def test_experience_metrics_tracking(self):
        """Experience events update metrics."""
        listener = EventListener.setup()
        bus = get_event_bus()

        bus.emit_sync(
            EventNames.EXPERIENCE_RECORDED,
            {"action": "search", "reward": 0.5, "success": True},
        )
        bus.emit_sync(
            EventNames.EXPERIENCE_RECORDED,
            {"action": "generate", "reward": 0.8, "success": True},
        )

        metrics = listener.get_metrics()
        assert metrics["learning"]["experiences"] == 2
        assert metrics["learning"]["total_rewards"] == 1.3

    def test_recent_events(self):
        """Recent events are tracked."""
        listener = EventListener.setup()
        bus = get_event_bus()

        for i in range(5):
            bus.emit_sync(EventNames.FLOW_STARTED, {"intent": f"test_{i}"})

        recent = listener.get_recent_events(limit=3)
        assert len(recent) == 3

    def test_reset_metrics(self):
        """Reset clears metrics."""
        listener = EventListener.setup()
        bus = get_event_bus()

        bus.emit_sync(
            EventNames.FLOW_COMPLETED,
            {"intent": "test", "duration_ms": 100, "success": True},
        )

        listener.reset_metrics()
        metrics = listener.get_metrics()
        assert metrics["flows"]["total"] == 0

    def test_intent_stats(self):
        """Per-intent statistics."""
        listener = EventListener.setup()
        bus = get_event_bus()

        bus.emit_sync(
            EventNames.FLOW_COMPLETED,
            {"intent": "qa_docs", "duration_ms": 100, "success": True},
        )
        bus.emit_sync(
            EventNames.FLOW_COMPLETED,
            {"intent": "qa_docs", "duration_ms": 200, "success": True},
        )
        bus.emit_sync(
            EventNames.FLOW_COMPLETED,
            {"intent": "analysis", "duration_ms": 300, "success": True},
        )

        metrics = listener.get_metrics()
        assert "qa_docs" in metrics["intents"]
        assert metrics["intents"]["qa_docs"]["count"] == 2
        assert metrics["intents"]["qa_docs"]["avg_duration_ms"] == 150.0


# ============================================================================
# Integration Test
# ============================================================================


def test_event_system_integration():
    """Full event system workflow."""
    reset_event_bus()
    EventListener._instance = None

    # Setup
    listener = EventListener.setup()
    bus = get_event_bus()

    custom_events = []

    @bus.on("custom.*")
    def track_custom(data):
        custom_events.append(data)

    # Simulate workflow
    bus.emit_sync(EventNames.SYSTEM_READY, {})
    bus.emit_sync(EventNames.PLUGIN_LOADED, {"name": "test-plugin", "action": "load"})
    bus.emit_sync(EventNames.FLOW_STARTED, {"intent": "qa", "query": "test"})
    bus.emit_sync(
        EventNames.FLOW_COMPLETED, {"intent": "qa", "duration_ms": 150, "success": True}
    )
    bus.emit_sync(
        EventNames.EXPERIENCE_RECORDED,
        {"action": "search", "reward": 0.9, "success": True},
    )
    bus.emit_sync("custom.event", {"data": "test"})

    # Verify
    metrics = listener.get_metrics()
    assert metrics["flows"]["total"] == 1
    assert metrics["flows"]["success"] == 1
    assert metrics["learning"]["experiences"] == 1
    assert metrics["plugins"]["loaded"] == 1
    assert len(custom_events) == 1

    # Cleanup
    reset_event_bus()
