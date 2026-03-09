"""
Unit Tests for Core A2A Module

Tests for agent-to-agent protocol support.
"""

import time
import pytest

from core.a2a import (
    AgentCard,
    AgentCapability,
    AgentDiscovery,
    AgentRegistration,
    A2AMessage,
    A2ARequest,
    A2AResponse,
    MessageType,
    ErrorCode,
)


# ============================================================================
# AgentCapability Tests
# ============================================================================


class TestAgentCapability:
    """Tests for AgentCapability dataclass."""

    def test_creation(self):
        """Basic creation."""
        cap = AgentCapability(
            name="search",
            description="Search capability",
        )

        assert cap.name == "search"
        assert cap.input_schema is None

    def test_creation_with_schemas(self):
        """Creation with schemas."""
        cap = AgentCapability(
            name="generate",
            description="Generate content",
            input_schema={
                "type": "object",
                "properties": {"prompt": {"type": "string"}},
            },
            output_schema={"type": "string"},
        )

        assert cap.input_schema is not None
        assert "prompt" in cap.input_schema["properties"]


# ============================================================================
# AgentCard Tests
# ============================================================================


class TestAgentCard:
    """Tests for AgentCard."""

    def test_creation(self):
        """Basic creation."""
        card = AgentCard(
            name="test_agent",
            description="A test agent",
        )

        assert card.name == "test_agent"
        assert card.version == "1.0.0"
        assert card.capabilities == []

    def test_creation_with_capabilities(self):
        """Creation with capabilities."""
        cap = AgentCapability(name="search", description="Search")
        card = AgentCard(
            name="search_agent",
            description="Search agent",
            capabilities=[cap],
        )

        assert len(card.capabilities) == 1

    def test_add_capability(self):
        """Add capability to card."""
        card = AgentCard(name="agent", description="An agent")

        card.add_capability(
            name="analyze",
            description="Analyze data",
        )

        assert len(card.capabilities) == 1
        assert card.capabilities[0].name == "analyze"

    def test_to_dict(self):
        """Convert to dictionary."""
        card = AgentCard(
            name="agent",
            description="Test agent",
            endpoint="http://localhost:8000",
        )
        card.add_capability("search", "Search capability")

        data = card.to_dict()

        assert data["name"] == "agent"
        assert data["endpoint"] == "http://localhost:8000"
        # capabilities is now the AgentCapabilities object
        assert "capabilities" in data
        assert "streaming" in data["capabilities"]
        # legacy capabilities are in legacyCapabilities
        assert len(data["legacyCapabilities"]) == 1

    def test_from_dict(self):
        """Create from dictionary."""
        data = {
            "name": "restored_agent",
            "description": "Restored from dict",
            "version": "2.0.0",
            "endpoint": "http://example.com",
            # New format: capabilities is AgentCapabilities object
            "capabilities": {"streaming": True},
            # Legacy capabilities go in legacyCapabilities
            "legacyCapabilities": [{"name": "cap1", "description": "Capability 1"}],
            "protocols": ["jsonrpc", "rest"],
        }

        card = AgentCard.from_dict(data)

        assert card.name == "restored_agent"
        assert card.version == "2.0.0"
        assert len(card.capabilities) == 1
        assert card.agentCapabilities.streaming is True

    def test_roundtrip_serialization(self):
        """to_dict and from_dict roundtrip."""
        original = AgentCard(
            name="roundtrip",
            description="Test roundtrip",
            endpoint="http://test.com",
        )
        original.add_capability("test", "Test cap")

        restored = AgentCard.from_dict(original.to_dict())

        assert restored.name == original.name
        assert restored.endpoint == original.endpoint


# ============================================================================
# AgentDiscovery Tests
# ============================================================================


class TestAgentDiscovery:
    """Tests for AgentDiscovery."""

    def test_initialization(self):
        """Basic initialization."""
        discovery = AgentDiscovery()

        assert len(discovery._agents) == 0

    def test_register(self):
        """Register an agent."""
        discovery = AgentDiscovery()
        card = AgentCard(name="agent1", description="First agent")

        discovery.register(card)

        assert discovery.get("agent1") == card

    def test_unregister_success(self):
        """Unregister existing agent."""
        discovery = AgentDiscovery()
        discovery.register(AgentCard(name="agent1", description="Test"))

        result = discovery.unregister("agent1")

        assert result is True
        assert discovery.get("agent1") is None

    def test_unregister_nonexistent(self):
        """Unregister nonexistent agent."""
        discovery = AgentDiscovery()

        result = discovery.unregister("nonexistent")

        assert result is False

    def test_find_by_capability(self):
        """Find agents by capability."""
        discovery = AgentDiscovery()

        card1 = AgentCard(name="searcher", description="Search agent")
        card1.add_capability("search", "Search the web")

        card2 = AgentCard(name="analyzer", description="Analysis agent")
        card2.add_capability("analyze", "Analyze data")

        card3 = AgentCard(name="multi", description="Multi-purpose")
        card3.add_capability("search", "Search")
        card3.add_capability("analyze", "Analyze")

        discovery.register(card1)
        discovery.register(card2)
        discovery.register(card3)

        searchers = discovery.find_by_capability("search")

        assert len(searchers) == 2
        names = [c.name for c in searchers]
        assert "searcher" in names
        assert "multi" in names

    def test_list_all(self):
        """List all registered agents."""
        discovery = AgentDiscovery()
        discovery.register(AgentCard(name="a1", description="Agent 1"))
        discovery.register(AgentCard(name="a2", description="Agent 2"))

        names = discovery.list_all()

        assert "a1" in names
        assert "a2" in names

    def test_get_all_cards(self):
        """Get all agent cards."""
        discovery = AgentDiscovery()
        discovery.register(AgentCard(name="a1", description="Agent 1"))
        discovery.register(AgentCard(name="a2", description="Agent 2"))

        cards = discovery.get_all_cards()

        assert len(cards) == 2


# ============================================================================
# Integration Test
# ============================================================================


def test_agent_discovery_workflow():
    """Full agent discovery workflow."""
    discovery = AgentDiscovery()

    # Create agents with capabilities
    qa_agent = AgentCard(
        name="qa_agent",
        description="Question answering agent",
        endpoint="http://qa-service:8000",
    )
    qa_agent.add_capability("answer", "Answer questions")
    qa_agent.add_capability("search", "Search knowledge base")

    gen_agent = AgentCard(
        name="generator",
        description="Content generation agent",
        endpoint="http://gen-service:8000",
    )
    gen_agent.add_capability("generate", "Generate content")

    # Register agents
    discovery.register(qa_agent)
    discovery.register(gen_agent)

    # Discover by capability
    search_capable = discovery.find_by_capability("search")
    assert len(search_capable) == 1
    assert search_capable[0].endpoint == "http://qa-service:8000"

    # Get agent for specific task
    generator = discovery.get("generator")
    assert generator is not None
    card_data = generator.to_dict()
    # Legacy capabilities are in legacyCapabilities
    assert "generate" in [c["name"] for c in card_data["legacyCapabilities"]]


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
# AgentRegistration Tests
# ============================================================================


class TestAgentRegistration:
    """Tests for AgentRegistration."""

    def test_creation(self):
        """Basic creation."""
        card = AgentCard(name="test", description="Test agent")
        reg = AgentRegistration(card=card)

        assert reg.card == card
        assert reg.is_healthy is True
        assert reg.failure_count == 0

    def test_heartbeat(self):
        """Test heartbeat update."""
        card = AgentCard(name="test", description="Test")
        reg = AgentRegistration(card=card)

        # Simulate time passing
        reg.last_seen = time.time() - 100

        reg.update_heartbeat()

        assert reg.seconds_since_seen < 1.0
        assert reg.is_healthy is True

    def test_failure_tracking(self):
        """Test failure tracking."""
        card = AgentCard(name="test", description="Test")
        reg = AgentRegistration(card=card)

        # Record failures
        reg.record_failure()
        assert reg.is_healthy is True  # Still healthy

        reg.record_failure()
        assert reg.is_healthy is True  # Still healthy

        reg.record_failure()
        assert reg.is_healthy is False  # Now unhealthy


# ============================================================================
# Enhanced AgentDiscovery Tests
# ============================================================================


class TestAgentDiscoveryHealth:
    """Tests for AgentDiscovery health features."""

    def test_heartbeat(self):
        """Test heartbeat update."""
        discovery = AgentDiscovery()
        discovery.register(AgentCard(name="agent1", description="Test"))

        result = discovery.heartbeat("agent1")
        assert result is True

        result = discovery.heartbeat("nonexistent")
        assert result is False

    def test_record_failure(self):
        """Test failure recording."""
        discovery = AgentDiscovery()
        discovery.register(AgentCard(name="agent1", description="Test"))

        # Record failures until unhealthy
        discovery.record_failure("agent1")
        discovery.record_failure("agent1")
        discovery.record_failure("agent1")

        reg = discovery.get_registration("agent1")
        assert reg is not None
        assert reg.is_healthy is False

    def test_list_healthy(self):
        """Test listing healthy agents."""
        discovery = AgentDiscovery()
        discovery.register(AgentCard(name="healthy1", description="Test"))
        discovery.register(AgentCard(name="healthy2", description="Test"))
        discovery.register(AgentCard(name="unhealthy", description="Test"))

        # Make one unhealthy
        for _ in range(3):
            discovery.record_failure("unhealthy")

        healthy = discovery.list_healthy()
        assert "healthy1" in healthy
        assert "healthy2" in healthy
        assert "unhealthy" not in healthy

    def test_find_by_capability_healthy_only(self):
        """Test finding by capability with health filter."""
        discovery = AgentDiscovery()

        card1 = AgentCard(name="healthy", description="Test")
        card1.add_capability("search", "Search")
        discovery.register(card1)

        card2 = AgentCard(name="unhealthy", description="Test")
        card2.add_capability("search", "Search")
        discovery.register(card2)

        # Make one unhealthy
        for _ in range(3):
            discovery.record_failure("unhealthy")

        # Healthy only (default)
        results = discovery.find_by_capability("search")
        assert len(results) == 1
        assert results[0].name == "healthy"

        # Include unhealthy
        results = discovery.find_by_capability("search", healthy_only=False)
        assert len(results) == 2

    def test_get_stats(self):
        """Test statistics."""
        discovery = AgentDiscovery()
        discovery.register(AgentCard(name="a1", description="Test"))
        discovery.register(AgentCard(name="a2", description="Test"))

        for _ in range(3):
            discovery.record_failure("a2")

        stats = discovery.get_stats()
        assert stats["total_agents"] == 2
        assert stats["healthy_agents"] == 1
        assert stats["unhealthy_agents"] == 1

    def test_event_callbacks(self):
        """Test registration/unregistration callbacks."""
        discovery = AgentDiscovery()
        registered = []
        unregistered = []

        discovery.on_register(lambda card: registered.append(card.name))
        discovery.on_unregister(lambda name: unregistered.append(name))

        discovery.register(AgentCard(name="agent1", description="Test"))
        discovery.unregister("agent1")

        assert "agent1" in registered
        assert "agent1" in unregistered


# ============================================================================
# AgentSkill Tests
# ============================================================================


class TestAgentSkill:
    """Tests for AgentSkill."""

    def test_creation(self):
        """Basic creation."""
        from core.a2a import AgentSkill

        skill = AgentSkill(
            id="search",
            name="Search",
            description="Search the web",
        )

        assert skill.id == "search"
        assert skill.name == "Search"
        assert skill.tags == []
        assert skill.inputModes == ["text/plain"]

    def test_with_tags_and_examples(self):
        """Creation with tags and examples."""
        from core.a2a import AgentSkill

        skill = AgentSkill(
            id="analyze",
            name="Analyze",
            description="Analyze data",
            tags=["data", "analysis"],
            examples=["Analyze this report", "What trends do you see?"],
        )

        assert len(skill.tags) == 2
        assert len(skill.examples) == 2

    def test_serialization(self):
        """Test to_dict and from_dict."""
        from core.a2a import AgentSkill

        original = AgentSkill(
            id="test",
            name="Test Skill",
            description="A test skill",
            tags=["test"],
        )

        data = original.to_dict()
        restored = AgentSkill.from_dict(data)

        assert restored.id == original.id
        assert restored.name == original.name


class TestAgentCardSkills:
    """Tests for AgentCard skill management."""

    def test_add_skill(self):
        """Add skill to card."""
        card = AgentCard(name="agent", description="Test")

        card.add_skill(
            id="search",
            name="Search",
            description="Search capability",
            tags=["search"],
        )

        assert len(card.skills) == 1
        assert card.skills[0].id == "search"

    def test_get_skill(self):
        """Get skill by ID."""
        card = AgentCard(name="agent", description="Test")
        card.add_skill("skill1", "Skill 1", "First skill")
        card.add_skill("skill2", "Skill 2", "Second skill")

        skill = card.get_skill("skill1")
        assert skill is not None
        assert skill.name == "Skill 1"

        missing = card.get_skill("nonexistent")
        assert missing is None

    def test_has_skill(self):
        """Check skill existence."""
        card = AgentCard(name="agent", description="Test")
        card.add_skill("present", "Present", "A skill")

        assert card.has_skill("present") is True
        assert card.has_skill("absent") is False


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
        from core.a2a import FilePart, FileContent

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
        from core.a2a import part_from_dict, TextPart, DataPart

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
        from core.a2a import Task, TaskState, Message

        task = Task.create()

        task.update_state(TaskState.WORKING)
        assert task.status.state == TaskState.WORKING

        response = Message.agent_message("Done!")
        task.update_state(TaskState.COMPLETED, response)
        assert task.status.state == TaskState.COMPLETED
        assert task.is_terminal is True

    def test_task_artifacts(self):
        """Test adding artifacts."""
        from core.a2a import Task, Artifact

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
        from core.a2a import Task, TaskState, Artifact

        task = Task.create(TaskState.COMPLETED)
        task.add_artifact(Artifact.text_artifact("Output"))

        data = task.to_dict()
        assert data["status"]["state"] == "completed"
        assert len(data["artifacts"]) == 1

        restored = Task.from_dict(data)
        assert restored.status.state == TaskState.COMPLETED


# ============================================================================
# JSON-RPC Tests
# ============================================================================


class TestJSONRPC:
    """Tests for JSON-RPC 2.0 structures."""

    def test_jsonrpc_request(self):
        """Test JSONRPCRequest."""
        from core.a2a import JSONRPCRequest

        req = JSONRPCRequest(
            method="message/send",
            params={"message": {"role": "user"}},
        )

        assert req.method == "message/send"
        assert req.jsonrpc == "2.0"

        data = req.to_dict()
        assert data["jsonrpc"] == "2.0"
        assert "id" in data

    def test_jsonrpc_request_factories(self):
        """Test JSONRPCRequest factory methods."""
        from core.a2a import JSONRPCRequest

        # message_send
        msg_req = JSONRPCRequest.message_send(
            message={"role": "user", "parts": []},
            context_id="ctx-123",
        )
        assert msg_req.method == "message/send"
        assert msg_req.params["contextId"] == "ctx-123"

        # tasks_get
        get_req = JSONRPCRequest.tasks_get("task-456")
        assert get_req.method == "tasks/get"
        assert get_req.params["id"] == "task-456"

        # tasks_cancel
        cancel_req = JSONRPCRequest.tasks_cancel("task-789")
        assert cancel_req.method == "tasks/cancel"

    def test_jsonrpc_response_success(self):
        """Test successful JSONRPCResponse."""
        from core.a2a import JSONRPCResponse

        resp = JSONRPCResponse.success("req-1", result={"data": "value"})

        assert resp.is_success is True
        assert resp.result == {"data": "value"}
        assert resp.error is None

    def test_jsonrpc_response_failure(self):
        """Test error JSONRPCResponse."""
        from core.a2a import JSONRPCResponse, JSONRPCError, ErrorCode

        error = JSONRPCError(ErrorCode.METHOD_NOT_FOUND, "Not found")
        resp = JSONRPCResponse.failure("req-1", error)

        assert resp.is_success is False
        assert resp.error.code == ErrorCode.METHOD_NOT_FOUND

    def test_jsonrpc_error_factories(self):
        """Test JSONRPCError factory methods."""
        from core.a2a import JSONRPCError, ErrorCode

        parse_err = JSONRPCError.parse_error()
        assert parse_err.code == ErrorCode.PARSE_ERROR

        method_err = JSONRPCError.method_not_found("unknown/method")
        assert "unknown/method" in method_err.message

        task_err = JSONRPCError.task_not_found("task-123")
        assert task_err.code == ErrorCode.TASK_NOT_FOUND


# ============================================================================
# A2AServer Tests
# ============================================================================


class TestA2AServer:
    """Tests for A2AServer."""

    @pytest.fixture
    def echo_server(self):
        """Create an echo server for testing."""
        from core.a2a import AgentCard, EchoA2AServer

        card = AgentCard(
            name="echo",
            description="Echo agent for testing",
        )
        return EchoA2AServer(card)

    @pytest.mark.asyncio
    async def test_message_send(self, echo_server):
        """Test message/send dispatch."""
        request = {
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "Hello!"}],
                    "messageId": "msg-1",
                }
            },
        }

        response = await echo_server.dispatch(request)

        assert response["jsonrpc"] == "2.0"
        assert response["id"] == "req-1"
        assert "result" in response
        assert response["result"]["status"]["state"] == "completed"

    @pytest.mark.asyncio
    async def test_tasks_get(self, echo_server):
        """Test tasks/get dispatch."""
        # First create a task
        send_request = {
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "Test"}],
                    "messageId": "msg-1",
                }
            },
        }
        send_response = await echo_server.dispatch(send_request)
        task_id = send_response["result"]["id"]

        # Then get the task
        get_request = {
            "jsonrpc": "2.0",
            "id": "req-2",
            "method": "tasks/get",
            "params": {"id": task_id},
        }

        response = await echo_server.dispatch(get_request)

        assert response["id"] == "req-2"
        assert response["result"]["id"] == task_id

    @pytest.mark.asyncio
    async def test_tasks_get_not_found(self, echo_server):
        """Test tasks/get with nonexistent task."""
        request = {
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "tasks/get",
            "params": {"id": "nonexistent-task"},
        }

        response = await echo_server.dispatch(request)

        assert "error" in response
        assert response["error"]["code"] == -32003  # TASK_NOT_FOUND

    @pytest.mark.asyncio
    async def test_method_not_found(self, echo_server):
        """Test unknown method."""
        request = {
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "unknown/method",
            "params": {},
        }

        response = await echo_server.dispatch(request)

        assert "error" in response
        assert response["error"]["code"] == -32601  # METHOD_NOT_FOUND

    @pytest.mark.asyncio
    async def test_invalid_request(self, echo_server):
        """Test invalid request."""
        request = {"invalid": "request"}

        response = await echo_server.dispatch(request)

        assert "error" in response
        assert response["error"]["code"] == -32600  # INVALID_REQUEST


# ============================================================================
# InMemoryTaskStore Tests
# ============================================================================


class TestInMemoryTaskStore:
    """Tests for InMemoryTaskStore."""

    @pytest.mark.asyncio
    async def test_save_and_get(self):
        """Test save and retrieve task."""
        from core.a2a import InMemoryTaskStore, Task, TaskState

        store = InMemoryTaskStore()
        task = Task.create(TaskState.WORKING)

        await store.save(task)
        retrieved = await store.get(task.id)

        assert retrieved is not None
        assert retrieved.id == task.id

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        """Test get nonexistent task."""
        from core.a2a import InMemoryTaskStore

        store = InMemoryTaskStore()
        result = await store.get("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self):
        """Test delete task."""
        from core.a2a import InMemoryTaskStore, Task

        store = InMemoryTaskStore()
        task = Task.create()
        await store.save(task)

        deleted = await store.delete(task.id)
        assert deleted is True

        retrieved = await store.get(task.id)
        assert retrieved is None

        # Delete again should return False
        deleted_again = await store.delete(task.id)
        assert deleted_again is False
