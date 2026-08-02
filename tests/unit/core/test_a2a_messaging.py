"""
Unit Tests for Core A2A Module - Messages, Parts, Tasks and Artifacts

Tests for the A2A message envelopes and the task/artifact payload types.
"""

from core.a2a import (
    A2AMessage,
    A2ARequest,
    A2AResponse,
    ErrorCode,
    MessageType,
)

# ============================================================================
# A2AMessage Tests
# ============================================================================


class TestA2AMessage:
    """Tests for A2AMessage."""

    def test_request_creation(self):
        """Create request message."""
        msg = A2AMessage.request(
            method="search",
            params={"query": "test"},
            from_agent="agent1",
            to_agent="agent2",
        )

        assert msg.type == MessageType.REQUEST
        assert msg.method == "search"
        assert msg.params == {"query": "test"}
        assert msg.from_agent == "agent1"
        assert msg.to_agent == "agent2"
        assert msg.id is not None

    def test_response_creation(self):
        """Create response message."""
        msg = A2AMessage.response(
            request_id="req-123",
            result={"data": "found"},
            from_agent="agent2",
        )

        assert msg.type == MessageType.RESPONSE
        assert msg.id == "req-123"
        assert msg.result == {"data": "found"}

    def test_error_response_creation(self):
        """Create error response."""
        msg = A2AMessage.error_response(
            request_id="req-456",
            code=ErrorCode.METHOD_NOT_FOUND,
            message="Method not supported",
        )

        assert msg.type == MessageType.ERROR
        assert msg.error["code"] == ErrorCode.METHOD_NOT_FOUND
        assert msg.error["message"] == "Method not supported"

    def test_serialization_roundtrip(self):
        """to_dict and from_dict roundtrip."""
        original = A2AMessage.request(
            method="invoke",
            params={"action": "test"},
        )

        data = original.to_dict()
        restored = A2AMessage.from_dict(data)

        assert restored.method == original.method
        assert restored.params == original.params
        assert restored.type == original.type


# ============================================================================
# A2ARequest/Response Tests
# ============================================================================


class TestA2ARequest:
    """Tests for A2ARequest."""

    def test_creation(self):
        """Basic creation."""
        req = A2ARequest(
            method="search",
            params={"query": "test"},
            timeout=10.0,
        )

        assert req.method == "search"
        assert req.timeout == 10.0

    def test_to_message(self):
        """Convert to A2AMessage."""
        req = A2ARequest(method="invoke", params={"x": 1})

        msg = req.to_message(from_agent="me", to_agent="you")

        assert msg.type == MessageType.REQUEST
        assert msg.method == "invoke"
        assert msg.from_agent == "me"


class TestA2AResponse:
    """Tests for A2AResponse."""

    def test_success_response(self):
        """Create successful response."""
        msg = A2AMessage.response("req-1", result={"success": True})
        resp = A2AResponse.from_message(msg, latency_ms=15.5)

        assert resp.success is True
        assert resp.result == {"success": True}
        assert resp.latency_ms == 15.5

    def test_error_response(self):
        """Create error response."""
        msg = A2AMessage.error_response("req-1", ErrorCode.TIMEOUT, "Timeout")
        resp = A2AResponse.from_message(msg)

        assert resp.success is False
        assert resp.error_code == ErrorCode.TIMEOUT
        assert resp.error_message == "Timeout"


# ============================================================================
# Part Types Tests
# ============================================================================


class TestPartTypes:
    """Tests for Part types."""

    def test_text_part(self):
        """Test TextPart."""
        from core.a2a import TextPart

        part = TextPart(text="Hello, world!")

        assert part.text == "Hello, world!"
        assert part.kind == "text"

        data = part.to_dict()
        assert data["kind"] == "text"
        assert data["text"] == "Hello, world!"

        restored = TextPart.from_dict(data)
        assert restored.text == part.text

    def test_file_part(self):
        """Test FilePart."""
        from core.a2a import FileContent, FilePart

        content = FileContent(
            name="test.txt",
            mimeType="text/plain",
            bytes="SGVsbG8gV29ybGQ=",  # Base64 "Hello World"
        )
        part = FilePart(file=content)

        assert part.kind == "file"
        assert part.file.name == "test.txt"

        data = part.to_dict()
        restored = FilePart.from_dict(data)
        assert restored.file.name == "test.txt"
        assert restored.file.bytes == "SGVsbG8gV29ybGQ="

    def test_data_part(self):
        """Test DataPart."""
        from core.a2a import DataPart

        part = DataPart(data={"key": "value", "count": 42})

        assert part.kind == "data"
        assert part.data["key"] == "value"

        data = part.to_dict()
        restored = DataPart.from_dict(data)
        assert restored.data["count"] == 42

    def test_part_from_dict(self):
        """Test part_from_dict factory."""
        from core.a2a import DataPart, TextPart, part_from_dict

        text_data = {"kind": "text", "text": "Hello"}
        text_part = part_from_dict(text_data)
        assert isinstance(text_part, TextPart)

        data_data = {"kind": "data", "data": {"x": 1}}
        data_part = part_from_dict(data_data)
        assert isinstance(data_part, DataPart)


# ============================================================================
# Message Tests
# ============================================================================


class TestMessage:
    """Tests for Message."""

    def test_user_message(self):
        """Create user message."""
        from core.a2a import Message, Role

        msg = Message.user_message("Hello, agent!")

        assert msg.role == Role.USER
        assert len(msg.parts) == 1
        assert msg.parts[0].text == "Hello, agent!"

    def test_agent_message(self):
        """Create agent message."""
        from core.a2a import Message, Role

        msg = Message.agent_message("Hello, user!")

        assert msg.role == Role.AGENT
        assert len(msg.parts) == 1

    def test_serialization(self):
        """Test Message serialization."""
        from core.a2a import Message

        original = Message.user_message("Test message")

        data = original.to_dict()
        assert data["role"] == "user"
        assert len(data["parts"]) == 1

        restored = Message.from_dict(data)
        assert restored.role == original.role
        assert restored.parts[0].text == "Test message"


# ============================================================================
# Task and Artifact Tests
# ============================================================================


class TestTaskAndArtifact:
    """Tests for Task and Artifact."""

    def test_task_creation(self):
        """Create task."""
        from core.a2a import Task, TaskState

        task = Task.create(TaskState.SUBMITTED)

        assert task.id is not None
        assert task.status.state == TaskState.SUBMITTED
        assert task.is_terminal is False

    def test_task_state_transitions(self):
        """Test task state transitions."""
        from core.a2a import Message, Task, TaskState

        task = Task.create()

        task.update_state(TaskState.WORKING)
        assert task.status.state == TaskState.WORKING

        response = Message.agent_message("Done!")
        task.update_state(TaskState.COMPLETED, response)
        assert task.status.state == TaskState.COMPLETED
        assert task.is_terminal is True

    def test_task_artifacts(self):
        """Test adding artifacts."""
        from core.a2a import Artifact, Task

        task = Task.create()

        artifact = Artifact.text_artifact(
            text="Result text",
            name="result.txt",
            description="The result",
        )
        task.add_artifact(artifact)

        assert len(task.artifacts) == 1
        assert task.artifacts[0].name == "result.txt"

    def test_task_serialization(self):
        """Test Task serialization."""
        from core.a2a import Artifact, Task, TaskState

        task = Task.create(TaskState.COMPLETED)
        task.add_artifact(Artifact.text_artifact("Output"))

        data = task.to_dict()
        assert data["status"]["state"] == "completed"
        assert len(data["artifacts"]) == 1

        restored = Task.from_dict(data)
        assert restored.status.state == TaskState.COMPLETED
