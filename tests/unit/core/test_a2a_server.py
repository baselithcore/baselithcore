"""
Unit Tests for Core A2A Module - Client, JSON-RPC, Server and Task Store

Tests for the transport surface: endpoint validation, JSON-RPC 2.0
structures, the A2A server dispatch loop and the in-memory task store.
"""

import pytest

from core.a2a import (
    A2AClient,
    AgentCard,
)


class TestA2AClientEndpointValidation:
    """Endpoint scheme validation guards against non-http(s) coercion."""

    def test_http_endpoint_allowed(self):
        client = A2AClient(
            AgentCard(name="a", description="d", endpoint="http://peer:8000")
        )
        assert client.endpoint == "http://peer:8000"

    def test_https_and_internal_host_allowed(self):
        # Private/internal hosts must stay allowed for A2A meshes.
        client = A2AClient(
            AgentCard(name="a", description="d", endpoint="https://10.0.0.5:8443")
        )
        assert client.endpoint == "https://10.0.0.5:8443"

    @pytest.mark.parametrize(
        "bad", ["file:///etc/passwd", "gopher://x/", "ftp://host/f"]
    )
    def test_non_http_scheme_rejected(self, bad):
        client = A2AClient(AgentCard(name="a", description="d", endpoint=bad))
        with pytest.raises(ValueError):
            _ = client.endpoint

    def test_missing_endpoint_rejected(self):
        client = A2AClient(AgentCard(name="a", description="d"))
        with pytest.raises(ValueError):
            _ = client.endpoint


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
        from core.a2a import ErrorCode, JSONRPCError, JSONRPCResponse

        error = JSONRPCError(ErrorCode.METHOD_NOT_FOUND, "Not found")
        resp = JSONRPCResponse.failure("req-1", error)

        assert resp.is_success is False
        assert resp.error.code == ErrorCode.METHOD_NOT_FOUND

    def test_jsonrpc_error_factories(self):
        """Test JSONRPCError factory methods."""
        from core.a2a import ErrorCode, JSONRPCError

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
    async def test_message_stream_dispatch(self, echo_server):
        """message/stream yields SSE events ending with final=true (no longer
        rejected with UNSUPPORTED_OPERATION)."""
        request = {
            "jsonrpc": "2.0",
            "id": "req-s",
            "method": "message/stream",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "stream me"}],
                    "messageId": "msg-s",
                }
            },
        }

        events = [ev async for ev in echo_server.dispatch_stream(request)]

        assert len(events) >= 2
        # Every event is a well-formed JSON-RPC response for this request.
        assert all(ev["jsonrpc"] == "2.0" and ev["id"] == "req-s" for ev in events)
        assert "error" not in events[0]
        # First event carries the task; terminal event is a final status-update.
        assert events[0]["result"]["kind"] == "task"
        final = events[-1]["result"]
        assert final["kind"] == "status-update"
        assert final["final"] is True
        assert final["status"]["state"] == "completed"

    @pytest.mark.asyncio
    async def test_message_stream_sync_dispatch_still_works(self, echo_server):
        """A sync (non-SSE) dispatch of message/stream returns the final task."""
        request = {
            "jsonrpc": "2.0",
            "id": "req-sy",
            "method": "message/stream",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "hi"}],
                    "messageId": "msg-sy",
                }
            },
        }
        response = await echo_server.dispatch(request)
        assert "error" not in response
        assert response["result"]["status"]["state"] == "completed"

    def test_agent_card_advertises_protocol_version(self):
        """AgentCard exposes protocolVersion (was missing → conformance gap)."""
        from core.a2a import AgentCard

        card = AgentCard(name="x", description="d")
        assert card.protocolVersion == "0.3.0"
        assert card.to_dict()["protocolVersion"] == "0.3.0"
        # Round-trips through from_dict.
        assert AgentCard.from_dict(card.to_dict()).protocolVersion == "0.3.0"

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
