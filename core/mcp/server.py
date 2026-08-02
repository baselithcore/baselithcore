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
import sys
from collections.abc import Callable, Coroutine
from typing import Any, get_type_hints

from core.config import get_mcp_config
from core.observability.logging import get_logger

from .handlers import MessageHandlerMixin
from .types import (
    MCPPrompt,
    MCPResource,
    MCPResourceTemplate,
    MCPServerInfo,
    MCPTool,
)

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
        autonomy_policy: Any | None = None,
    ) -> None:
        """Initialize MCP Server.

        Args:
            name: Server name for identification (defaults to config)
            version: Server version string (defaults to config)
            autonomy_policy: Optional ``core.orchestration.autonomy.AutonomyPolicy``.
                When set, tool calls whose category requires approval at the
                policy's level are rejected (MCP transports have no human
                approval channel, so the gate is fail-closed).
        """
        config = get_mcp_config()
        self.config = config

        server_name = name or config.mcp_server_name
        server_version = version or config.mcp_server_version

        self.info = MCPServerInfo(name=server_name, version=server_version)
        self._tools: dict[str, MCPTool] = {}
        self._resources: dict[str, MCPResource] = {}
        self._resource_templates: dict[str, MCPResourceTemplate] = {}
        self._prompts: dict[str, MCPPrompt] = {}
        self._running = False
        self._request_id = 0
        self._autonomy_policy = autonomy_policy

    # -------------------------------------------------------------------------
    # Tool Registration
    # -------------------------------------------------------------------------

    @staticmethod
    def _compile_schema(schema: dict[str, Any] | None) -> Any:
        """Compile *schema* once, so the hot path never re-parses it.

        SEP-1613 makes JSON Schema 2020-12 the default MCP dialect: a Draft-7
        validator silently ignores 2019-09+ keywords such as ``prefixItems``,
        so those constraints would go unenforced. An explicit ``$schema`` still
        wins via ``validator_for``.
        """
        if not isinstance(schema, dict) or not schema:
            return None
        from jsonschema.validators import Draft202012Validator, validator_for

        validator_cls = validator_for(schema, default=Draft202012Validator)
        return validator_cls(schema)

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[..., Coroutine[Any, Any, Any]],
        category: str = "read_only",
        output_schema: dict[str, Any] | None = None,
        icons: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Register a tool with the MCP server.

        Args:
            name: Unique tool name
            description: Human-readable description
            input_schema: JSON Schema for tool inputs
            handler: Async function to execute the tool
            category: Autonomy category (read_only | mutating | destructive |
                external_side_effect) consulted by the approval gate.
            output_schema: Optional JSON Schema for the tool's structured
                result. When set, the handler must return a mapping matching
                it and ``tools/call`` emits ``structuredContent``.
            icons: Optional display icons (SEP-973), each
                ``{"src", "mimeType", "sizes"}``.
        """
        self._tools[name] = MCPTool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            category=category,
            output_schema=output_schema,
            icons=icons,
            validator=self._compile_schema(input_schema),
            output_validator=self._compile_schema(output_schema),
        )
        logger.info("mcp_tool_registered", tool_name=name, category=category)

    def tool(
        self,
        name: str | None = None,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
        category: str = "read_only",
        output_schema: dict[str, Any] | None = None,
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

            self.register_tool(
                tool_name,
                tool_description,
                schema,
                func,
                category=category,
                output_schema=output_schema,
            )
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
        handler: Callable[..., Coroutine[Any, Any, str]],
        mime_type: str = "text/plain",
        icons: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Register a resource with the MCP server.

        Args:
            uri: Unique resource URI (e.g., mcp://docs/nav)
            name: Human-readable name
            description: Resource description
            handler: Async function to return resource content
            mime_type: Content MIME type
            icons: Optional display icons (SEP-973)
        """
        self._resources[uri] = MCPResource(
            uri=uri,
            name=name,
            description=description,
            mime_type=mime_type,
            handler=handler,
            icons=icons,
        )
        logger.info("mcp_resource_registered", uri=uri, name=name)

    def register_resource_template(
        self,
        uri_template: str,
        name: str,
        description: str,
        handler: Callable[..., Coroutine[Any, Any, str]],
        mime_type: str = "text/plain",
        icons: list[dict[str, Any]] | None = None,
        completions: dict[str, Any] | None = None,
    ) -> None:
        """
        Register a parameterized resource family.

        Args:
            uri_template: RFC 6570 Level-1 template, e.g.
                ``mcp://reports/{year}/{month}``. Each variable matches a
                single path segment.
            name: Human-readable name
            description: Template description
            handler: Async function called as ``handler(uri, **variables)``
            mime_type: Content MIME type of the produced resources
            icons: Optional display icons (SEP-973)
            completions: Per-variable ``completion/complete`` providers — a
                static list of candidates or a callable taking the partial
                value typed so far.
        """
        from core.mcp.uri_template import compile_template

        self._resource_templates[uri_template] = MCPResourceTemplate(
            uri_template=uri_template,
            name=name,
            description=description,
            mime_type=mime_type,
            handler=handler,
            icons=icons,
            completions=completions,
            pattern=compile_template(uri_template),
        )
        logger.info("mcp_resource_template_registered", uri_template=uri_template)

    def register_prompt(
        self,
        name: str,
        description: str,
        arguments: list[dict[str, Any]],
        handler: Callable[..., Coroutine[Any, Any, Any]],
        icons: list[dict[str, Any]] | None = None,
        completions: dict[str, Any] | None = None,
    ) -> None:
        """
        Register a prompt template.

        Args:
            name: Unique prompt name
            description: What the prompt is for
            arguments: Argument descriptors, each
                ``{"name", "description", "required"}``
            handler: Async function receiving the arguments as keywords and
                returning either a string (rendered as one user message) or an
                explicit list of ``PromptMessage`` dicts
            icons: Optional display icons (SEP-973)
            completions: Per-argument ``completion/complete`` providers — a
                static list of candidates or a callable taking the partial
                value typed so far.
        """
        self._prompts[name] = MCPPrompt(
            name=name,
            description=description,
            arguments=arguments,
            handler=handler,
            icons=icons,
            completions=completions,
        )
        logger.info("mcp_prompt_registered", prompt_name=name)

    def resource(
        self,
        uri: str,
        name: str | None = None,
        description: str = "",
        mime_type: str = "text/plain",
    ) -> Callable[
        [Callable[..., Coroutine[Any, Any, str]]],
        Callable[..., Coroutine[Any, Any, str]],
    ]:
        """
        Decorator to register a function as an MCP resource provider.

        Usage:
            @server.resource(uri="mcp://config", name="App Config")
            async def get_config(uri: str) -> str:
                ...
        """

        def decorator(
            func: Callable[..., Coroutine[Any, Any, str]],
        ) -> Callable[..., Coroutine[Any, Any, str]]:
            res_name = name or func.__name__
            res_description = description or func.__doc__ or ""

            self.register_resource(uri, res_name, res_description, func, mime_type)
            return func

        return decorator

    # -------------------------------------------------------------------------
    # Stdio Transport (for Claude Desktop)
    # -------------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the serve loop should keep reading."""
        return self._running

    async def run_stdio(self) -> None:
        """
        Run the MCP server using stdio transport.

        This is the transport mode used by Claude Desktop. Requests are served
        concurrently and are cancellable via ``notifications/cancelled``.
        """
        from core.mcp.stdio_server import serve_stdio

        self._running = True
        await serve_stdio(self)

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
    from core.observability.setup import ensure_logging_configured

    ensure_logging_configured(stream=sys.stderr)

    server = create_default_server()
    await server.run(transport="stdio")


if __name__ == "__main__":
    asyncio.run(main())
