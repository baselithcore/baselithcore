"""
Tests for MCP Server module.
"""

import pytest
import json

from core.mcp.server import MCPServer, create_default_server


class TestMCPServer:
    """Test suite for MCP Server."""

    def test_server_initialization(self) -> None:
        """Test server initializes with correct defaults."""
        server = MCPServer()

        assert server.info.name == "baselith-core"
        assert server.info.version == "2.0.0"
        assert server.info.capabilities.tools is True
        assert server.info.capabilities.resources is True

    def test_server_custom_name(self) -> None:
        """Test server with custom name and version."""
        server = MCPServer(name="custom-server", version="1.0.0")

        assert server.info.name == "custom-server"
        assert server.info.version == "1.0.0"

    def test_register_tool(self) -> None:
        """Test tool registration."""
        server = MCPServer()

        async def test_handler(message: str) -> str:
            return f"Echo: {message}"

        server.register_tool(
            name="test_tool",
            description="A test tool",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
            },
            handler=test_handler,
        )

        assert "test_tool" in server._tools
        assert server._tools["test_tool"].description == "A test tool"

    def test_tool_decorator(self) -> None:
        """Test tool decorator registration."""
        server = MCPServer()

        @server.tool(name="decorated_tool", description="A decorated tool")
        async def my_tool(query: str) -> str:
            return f"Result: {query}"

        assert "decorated_tool" in server._tools
        assert server._tools["decorated_tool"].handler is my_tool

    def test_register_resource(self) -> None:
        """Test resource registration."""
        server = MCPServer()

        async def mock_handler(*args, **kwargs):
            return "content"

        server.register_resource(
            uri="file:///test.txt",
            name="Test File",
            description="A test file",
            handler=mock_handler,
            mime_type="text/plain",
        )

        assert "file:///test.txt" in server._resources
        assert server._resources["file:///test.txt"].name == "Test File"

    @pytest.mark.asyncio
    async def test_handle_initialize(self) -> None:
        """Test initialize message handling."""
        server = MCPServer()

        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        }

        response = await server.handle_message(message)

        assert response is not None
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert "result" in response
        assert response["result"]["protocolVersion"] == "2024-11-05"
        assert response["result"]["serverInfo"]["name"] == "baselith-core"

    @pytest.mark.asyncio
    async def test_handle_list_tools(self) -> None:
        """Test tools/list message handling."""
        server = MCPServer()

        @server.tool(name="test", description="Test tool")
        async def test_tool() -> str:
            return "test"

        message = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }

        response = await server.handle_message(message)

        assert response is not None
        assert "result" in response
        assert "tools" in response["result"]
        assert len(response["result"]["tools"]) == 1
        assert response["result"]["tools"][0]["name"] == "test"

    @pytest.mark.asyncio
    async def test_handle_call_tool(self) -> None:
        """Test tools/call message handling."""
        server = MCPServer()

        @server.tool(name="echo", description="Echo tool")
        async def echo_tool(message: str) -> str:
            return f"Echo: {message}"

        message = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "hello"}},
        }

        response = await server.handle_message(message)

        assert response is not None
        assert "result" in response
        assert response["result"]["isError"] is False
        assert len(response["result"]["content"]) == 1
        assert response["result"]["content"][0]["text"] == "Echo: hello"

    @pytest.mark.asyncio
    async def test_handle_unknown_method(self) -> None:
        """Test handling of unknown method."""
        server = MCPServer()

        message = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "unknown/method",
            "params": {},
        }

        response = await server.handle_message(message)

        assert response is not None
        assert "error" in response
        assert response["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_handle_ping(self) -> None:
        """Test ping message handling."""
        server = MCPServer()

        message = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "ping",
            "params": {},
        }

        response = await server.handle_message(message)

        assert response is not None
        assert response["result"]["pong"] is True


class TestCreateDefaultServer:
    """Test suite for create_default_server function."""

    def test_creates_server_with_tools(self) -> None:
        """Test that default server has built-in tools."""
        server = create_default_server()

        assert "echo" in server._tools
        assert "get_system_info" in server._tools

    @pytest.mark.asyncio
    async def test_echo_tool_works(self) -> None:
        """Test that echo tool works correctly."""
        server = create_default_server()

        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "test"}},
        }

        response = await server.handle_message(message)

        assert response is not None
        assert "Echo: test" in response["result"]["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_get_system_info_works(self) -> None:
        """Test that get_system_info tool works correctly."""
        server = create_default_server()

        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_system_info", "arguments": {}},
        }

        response = await server.handle_message(message)

        assert response is not None
        result_text = response["result"]["content"][0]["text"]
        result = json.loads(result_text)
        assert result["name"] == "Baselith-Core"
        assert "RAG" in result["capabilities"]
