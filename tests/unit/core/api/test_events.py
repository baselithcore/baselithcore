"""
Tests for core.api.events module.
"""

from core.api.events import EventType, AgentEvent


class TestEventType:
    """Tests for EventType enum."""

    def test_event_types_exist(self):
        """Test all event types are defined."""
        assert EventType.THOUGHT.value == "thought"
        assert EventType.TOOL_CALL.value == "tool_call"
        assert EventType.TOOL_RESULT.value == "tool_result"
        assert EventType.MEMORY.value == "memory"
        assert EventType.HUMAN_REQUEST.value == "human"
        assert EventType.RESPONSE_CHUNK.value == "chunk"
        assert EventType.RESPONSE_FINAL.value == "final"
        assert EventType.ERROR.value == "error"


class TestAgentEvent:
    """Tests for AgentEvent dataclass."""

    def test_create_event(self):
        """Test creating an event with defaults."""
        event = AgentEvent(type=EventType.THOUGHT)

        assert event.type == EventType.THOUGHT
        assert event.content is None
        assert event.data == {}
        assert event.agent_id == "system"
        assert event.id is not None
        assert event.timestamp is not None

    def test_create_event_with_content(self):
        """Test creating event with content."""
        event = AgentEvent(
            type=EventType.RESPONSE_CHUNK,
            content="Hello world",
            agent_id="agent-1",
        )

        assert event.content == "Hello world"
        assert event.agent_id == "agent-1"

    def test_create_event_with_data(self):
        """Test creating event with data payload."""
        event = AgentEvent(
            type=EventType.TOOL_CALL,
            data={"tool": "search", "args": {"query": "test"}},
        )

        assert event.data["tool"] == "search"
        assert event.data["args"]["query"] == "test"

    def test_to_dict(self):
        """Test converting event to dictionary."""
        event = AgentEvent(
            type=EventType.RESPONSE_FINAL,
            content="Final answer",
            agent_id="agent-2",
        )

        d = event.to_dict()

        assert d["type"] == "final"
        assert d["content"] == "Final answer"
        assert d["agent_id"] == "agent-2"
        assert "id" in d
        assert "timestamp" in d
        assert "data" in d

    def test_to_dict_includes_all_fields(self):
        """Test to_dict includes all required fields."""
        event = AgentEvent(type=EventType.ERROR, content="An error occurred")

        d = event.to_dict()

        required_keys = ["id", "type", "agent_id", "timestamp", "content", "data"]
        for key in required_keys:
            assert key in d
