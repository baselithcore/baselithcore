"""
Unit Tests for the Event Bus.

Tests for EventBus publish/subscribe semantics and the global bus singleton.
"""

import asyncio
from unittest.mock import Mock, patch

import pytest

from core.events import (
    EventBus,
    EventNames,
    get_event_bus,
    reset_event_bus,
)

# ============================================================================
# EventBus Tests
# ============================================================================


class TestEventBus:
    """Tests for EventBus."""

    def setup_method(self):
        """Reset global event bus before each test."""
        reset_event_bus()

    @patch("core.events._singleton.get_events_config")
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

    def test_unsubscribe_invalidates_cached_handlers(self):
        """Cached handler resolution is refreshed after unsubscribe."""
        bus = EventBus()
        received = []

        def handler(data):
            received.append(data["step"])

        unsubscribe = bus.subscribe("cached.unsub", handler)
        bus.emit_sync("cached.unsub", {"step": "before"})
        unsubscribe()
        bus.emit_sync("cached.unsub", {"step": "after"})

        assert received == ["before"]

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

    def test_clear_handlers_invalidates_cached_handlers(self):
        """Clearing handlers invalidates cached event lookups."""
        bus = EventBus()
        received = []

        bus.subscribe("clear.cached", lambda d: received.append(d["step"]))
        bus.emit_sync("clear.cached", {"step": "before"})
        bus.clear_handlers("clear.cached")
        bus.emit_sync("clear.cached", {"step": "after"})

        assert received == ["before"]

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
