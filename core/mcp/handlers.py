"""MCP Message Handlers.

Contains the JSON-RPC message routing and handling logic.
"""

from __future__ import annotations

import json
from core.observability.logging import get_logger
from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class MessageHandlerMixin:
    """Mixin providing MCP message handling functionality.

    Handles JSON-RPC message routing for MCP protocol methods.
    """

    # These will be provided by the main class
    info: Any
    _tools: Dict[str, Any]
    _resources: Dict[str, Any]
    _autonomy_policy: Any

    async def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """
        Handle an incoming MCP JSON-RPC message.

        Args:
            message: Parsed JSON-RPC message

        Returns:
            Response message or None for notifications
        """
        method = message.get("method", "")
        params = message.get("params", {})
        msg_id = message.get("id")

        logger.debug(f"MCP message received: method={method}, id={msg_id}")

        try:
            # Route to appropriate handler
            if method == "initialize":
                result = await self._handle_initialize(params)
            elif method == "tools/list":
                result = await self._handle_list_tools()
            elif method == "tools/call":
                result = await self._handle_call_tool(params)
            elif method == "resources/list":
                result = await self._handle_list_resources()
            elif method == "resources/read":
                result = await self._handle_read_resource(params)
            elif method == "ping":
                result = {"pong": True}
            elif method == "notifications/initialized":
                # Client notification - no response needed
                logger.info("MCP client initialized")
                return None
            else:
                return self._error_response(
                    msg_id, -32601, f"Method not found: {method}"
                )

            return self._success_response(msg_id, result)

        except Exception as e:
            logger.exception(f"MCP handler error: method={method}, error={e}")
            return self._error_response(msg_id, -32603, str(e))

    async def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle initialize request."""
        client_info = params.get("clientInfo", {})
        logger.info(
            f"MCP initialize: client={client_info.get('name')} v{client_info.get('version')}"
        )

        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": {
                "name": self.info.name,
                "version": self.info.version,
            },
            "capabilities": {
                "tools": {"listChanged": True}
                if self.info.capabilities.tools
                else None,
                "resources": {"listChanged": True}
                if self.info.capabilities.resources
                else None,
                "prompts": {} if self.info.capabilities.prompts else None,
                "logging": {} if self.info.capabilities.logging else None,
            },
        }

    async def _handle_list_tools(self) -> dict[str, Any]:
        """Handle tools/list request."""
        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]
        return {"tools": tools}

    async def _handle_call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/call request."""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name not in self._tools:
            raise ValueError(f"Unknown tool: {tool_name}")

        tool = self._tools[tool_name]
        if tool.handler is None:
            raise ValueError(f"Tool {tool_name} has no handler")

        if not isinstance(arguments, dict):
            raise ValueError(f"Invalid arguments for tool {tool_name}: expected object")

        # Prefer the validator compiled once at registration; fall back to a
        # one-off validate() for tools constructed without a cached validator.
        validator = getattr(tool, "validator", None)
        schema = getattr(tool, "input_schema", None)
        if validator is not None:
            from jsonschema import ValidationError

            try:
                validator.validate(arguments)
            except ValidationError as exc:
                raise ValueError(
                    f"Invalid arguments for tool {tool_name}: {exc.message}"
                ) from exc
        elif isinstance(schema, dict) and schema:
            from jsonschema import ValidationError, validate

            try:
                validate(instance=arguments, schema=schema)
            except ValidationError as exc:
                raise ValueError(
                    f"Invalid arguments for tool {tool_name}: {exc.message}"
                ) from exc

        # Autonomy gate — fail-closed: MCP transports carry no human-approval
        # channel, so categories requiring approval at the active level are
        # rejected outright instead of executing unsupervised.
        policy = getattr(self, "_autonomy_policy", None)
        if policy is not None:
            category = getattr(tool, "category", "read_only")
            if policy.requires_approval(category):
                logger.warning(
                    "mcp_tool_blocked_by_autonomy_policy",
                    tool_name=tool_name,
                    category=category,
                    level=policy.level.name,
                )
                raise PermissionError(
                    f"Tool '{tool_name}' (category={category}) requires human "
                    f"approval at autonomy level {policy.level.name}; MCP "
                    "transport has no approval channel."
                )

        logger.info(f"MCP tool call: tool={tool_name}, arguments={arguments}")

        # Execute the tool
        result = await tool.handler(**arguments)

        # Format result as MCP content
        if isinstance(result, str):
            content = [{"type": "text", "text": result}]
        elif isinstance(result, dict):
            content = [{"type": "text", "text": json.dumps(result)}]
        elif isinstance(result, list):
            content = [{"type": "text", "text": json.dumps(result)}]
        else:
            content = [{"type": "text", "text": str(result)}]

        return {"content": content, "isError": False}

    async def _handle_list_resources(self) -> dict[str, Any]:
        """Handle resources/list request."""
        resources = [
            {
                "uri": res.uri,
                "name": res.name,
                "description": res.description,
                "mimeType": res.mime_type,
            }
            for res in self._resources.values()
        ]
        return {"resources": resources}

    async def _handle_read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle resources/read request by executing the registered handler."""
        uri = params.get("uri", "")

        if uri not in self._resources:
            raise ValueError(f"Unknown resource: {uri}")

        resource = self._resources[uri]
        if resource.handler is None:
            raise ValueError(f"Resource {uri} has no read handler")

        logger.info(f"MCP resource read: uri={uri}")

        # Execute the resource handler
        content = await resource.handler(uri)

        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": resource.mime_type,
                    "text": content,
                }
            ]
        }

    def _success_response(self, msg_id: Any, result: Any) -> dict[str, Any]:
        """Create a success response."""
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": result,
        }

    def _error_response(self, msg_id: Any, code: int, message: str) -> dict[str, Any]:
        """Create an error response."""
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {
                "code": code,
                "message": message,
            },
        }
