"""
Model Context Protocol (MCP) Integration Bridge.

Transforms internal BaselithCore capabilities into standardized MCP
endpoints. Enables seamless interoperability with third-party tools
(Claude Desktop, IDEs) by exposing tools and resources via regularized
JSON-RPC transports.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sys
from typing import Any, Callable, Coroutine, get_type_hints

from core.observability.logging import get_logger
from core.config import get_mcp_config
from .types import MCPTool, MCPResource, MCPServerInfo
from .handlers import MessageHandlerMixin

logger = get_logger(__name__)


class MCPServer(MessageHandlerMixin):
    """
    Protocol adapter for external tool use.

    Implements the core MCP server specification, managing tool
    discovery, schema generation, and request routing. Supports stdio
    transport for local integration and is extensible for network-based
    transports like SSE.
    """

    def __init__(
        self,
        name: str | None = None,
        version: str | None = None,
    ) -> None:
        """Initialize MCP Server.

        Args:
            name: Server name for identification (defaults to config)
            version: Server version string (defaults to config)
        """
        config = get_mcp_config()
        self.config = config

        server_name = name or config.mcp_server_name
        server_version = version or config.mcp_server_version

        self.info = MCPServerInfo(name=server_name, version=server_version)
        self._tools: dict[str, MCPTool] = {}
        self._resources: dict[str, MCPResource] = {}
        self._running = False
        self._request_id = 0

    # -------------------------------------------------------------------------
    # Tool Registration
    # -------------------------------------------------------------------------

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[..., Coroutine[Any, Any, Any]],
    ) -> None:
        """
        Register a tool with the MCP server.

        Args:
            name: Unique tool name
            description: Human-readable description
            input_schema: JSON Schema for tool inputs
            handler: Async function to execute the tool
        """
        self._tools[name] = MCPTool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
        )
        logger.info("mcp_tool_registered", tool_name=name)

    def tool(
        self,
        name: str | None = None,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
    ) -> Callable[
        [Callable[..., Coroutine[Any, Any, Any]]],
        Callable[..., Coroutine[Any, Any, Any]],
    ]:
        """
        Decorator to register a function as an MCP tool.

        Usage:
            @server.tool(name="search", description="Search documents")
            async def search(query: str) -> list[dict]:
                ...
        """

        def decorator(
            func: Callable[..., Coroutine[Any, Any, Any]],
        ) -> Callable[..., Coroutine[Any, Any, Any]]:
            tool_name = name or func.__name__
            tool_description = description or func.__doc__ or ""

            # Auto-generate schema from function signature if not provided
            schema = input_schema or self._generate_schema_from_function(func)

            self.register_tool(tool_name, tool_description, schema, func)
            return func

        return decorator

    def _generate_schema_from_function(
        self, func: Callable[..., Any]
    ) -> dict[str, Any]:
        """Generate JSON Schema from function type hints."""
        hints = get_type_hints(func) if hasattr(func, "__annotations__") else {}
        sig = inspect.signature(func)

        properties: dict[str, Any] = {}
        required: list[str] = []

        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
        }

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            param_type = hints.get(param_name, Any)
            json_type = type_map.get(param_type, "string")

            properties[param_name] = {"type": json_type}

            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    # -------------------------------------------------------------------------
    # Resource Registration
    # -------------------------------------------------------------------------

    def register_resource(
        self,
        uri: str,
        name: str,
        description: str,
        mime_type: str = "text/plain",
    ) -> None:
        """Register a resource with the MCP server."""
        self._resources[uri] = MCPResource(
            uri=uri,
            name=name,
            description=description,
            mime_type=mime_type,
        )
        logger.info("mcp_resource_registered", uri=uri, name=name)

    # -------------------------------------------------------------------------
    # Stdio Transport (for Claude Desktop)
    # -------------------------------------------------------------------------

    async def run_stdio(self) -> None:
        """
        Run the MCP server using stdio transport.

        This is the transport mode used by Claude Desktop.
        Messages are read from stdin and written to stdout as JSON-RPC.
        """
        self._running = True
        logger.info("mcp_server_starting", transport="stdio", name=self.info.name)

        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        (
            writer_transport,
            writer_protocol,
        ) = await asyncio.get_running_loop().connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout
        )
        writer = asyncio.StreamWriter(
            writer_transport, writer_protocol, reader, asyncio.get_running_loop()
        )

        try:
            while self._running:
                # Read a line from stdin
                line = await reader.readline()
                if not line:
                    break

                try:
                    message = json.loads(line.decode().strip())
                    response = await self.handle_message(message)

                    if response is not None:
                        response_line = json.dumps(response) + "\n"
                        writer.write(response_line.encode())
                        await writer.drain()

                except json.JSONDecodeError as e:
                    logger.warning("mcp_invalid_json", error=str(e))
                    error_response = self._error_response(None, -32700, "Parse error")
                    writer.write((json.dumps(error_response) + "\n").encode())
                    await writer.drain()

        except asyncio.CancelledError:
            logger.info("mcp_server_cancelled")
        finally:
            self._running = False
            logger.info("mcp_server_stopped")

    async def run(self, transport: str = "stdio") -> None:
        """
        Run the MCP server.

        Args:
            transport: Transport type ("stdio" or "sse")
        """
        if transport == "stdio":
            await self.run_stdio()
        else:
            raise ValueError(f"Unsupported transport: {transport}")

    def stop(self) -> None:
        """Stop the MCP server."""
        self._running = False


# ============================================================================
# Default Server with Built-in Tools
# ============================================================================


def create_default_server() -> MCPServer:
    """
    Create an MCP server with default tools from the Baselith-Core.

    Returns:
        Configured MCPServer instance
    """
    server = MCPServer()

    @server.tool(
        name="echo",
        description="Echo back the input message",
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to echo"}
            },
            "required": ["message"],
        },
    )
    async def echo(message: str) -> str:
        return f"Echo: {message}"

    @server.tool(
        name="get_system_info",
        description="Get information about the Baselith-Core",
        input_schema={"type": "object", "properties": {}},
    )
    async def get_system_info() -> dict[str, Any]:
        return {
            "name": "Baselith-Core",
            "version": "2.0.0",
            "capabilities": [
                "RAG",
                "Knowledge Graph",
                "Tree of Thoughts",
                "Code Execution",
                "Web Scraping",
            ],
        }

    return server


# ============================================================================
# Entry Point
# ============================================================================


async def main() -> None:
    """Main entry point for running the MCP server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    server = create_default_server()
    await server.run(transport="stdio")


if __name__ == "__main__":
    asyncio.run(main())
