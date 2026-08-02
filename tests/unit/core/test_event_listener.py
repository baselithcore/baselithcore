"""
Unit Tests for the Event Listener.

Tests for EventMetrics aggregation and the EventListener wiring on the bus.
"""

from core.events import (
    EventNames,
    get_event_bus,
    reset_event_bus,
)
from core.events.listener import EventListener, EventMetrics

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
