"""MCP Message Handlers.

Contains the JSON-RPC message routing and handling logic.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# MCP protocol versions this server speaks, newest first. The server negotiates
# by echoing the client's requested version when supported, else offering its
# latest. 2025-11-25 adds Implementation.description, icons metadata and the
# SEP-1303 rule (input-validation failures are tool execution errors, not
# protocol errors); 2025-06-18 adds tool annotations (behavioural hints) and
# structured tool output; 2024-11-05 is retained for backward compatibility.
LATEST_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)


# RFC 5424 severities, as enumerated by the MCP `LoggingLevel` schema.
_LOG_LEVELS = (
    "debug",
    "info",
    "notice",
    "warning",
    "error",
    "critical",
    "alert",
    "emergency",
)


def _tool_annotations(category: str) -> dict[str, Any]:
    """Derive MCP tool-behaviour annotations from the tool's autonomy category.

    Maps ``core.mcp.types.MCPTool.category`` (read_only | mutating | destructive
    | external_side_effect) to the 2025-06-18 annotation hints so clients can
    reason about a tool's side effects (e.g. auto-approve read-only, confirm
    destructive) without executing it.
    """
    read_only = category == "read_only"
    destructive = category in ("destructive", "external_side_effect")
    return {
        # A read-only tool does not modify its environment.
        "readOnlyHint": read_only,
        # Destructive tools may perform irreversible updates (only meaningful
        # when not read-only).
        "destructiveHint": destructive,
        # Reads are idempotent; writes are not assumed to be.
        "idempotentHint": read_only,
        # External side effects touch entities outside the local system.
        "openWorldHint": category == "external_side_effect",
    }


class MessageHandlerMixin:
    """Mixin providing MCP message handling functionality.

    Handles JSON-RPC message routing for MCP protocol methods.
    """

    # These will be provided by the main class
    info: Any
    _tools: dict[str, Any]
    _resources: dict[str, Any]
    _autonomy_policy: Any
    # Minimum severity the client asked to receive (logging/setLevel).
    _log_level: str = "info"

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
                # Spec: the receiver responds with an *empty* result.
                result = {}
            elif method == "logging/setLevel":
                result = await self._handle_set_level(params)
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
        """Handle initialize request with protocol-version negotiation."""
        client_info = params.get("clientInfo", {})
        requested = params.get("protocolVersion")
        # Echo the client's version when we support it; otherwise offer our
        # latest and let the client decide whether to proceed.
        negotiated = (
            requested
            if requested in SUPPORTED_PROTOCOL_VERSIONS
            else LATEST_PROTOCOL_VERSION
        )
        logger.info(
            "MCP initialize: client=%s v%s requested=%s negotiated=%s",
            client_info.get("name"),
            client_info.get("version"),
            requested,
            negotiated,
        )

        # `ServerCapabilities` members are objects or absent — never JSON null,
        # which strictly-typed clients reject. Sub-capabilities are advertised
        # only when actually implemented: `listChanged` is omitted because the
        # server emits no list_changed notifications, so a client that trusted
        # the flag would wait instead of re-polling.
        declared = self.info.capabilities
        capabilities: dict[str, Any] = {}
        if declared.tools:
            capabilities["tools"] = {}
        if declared.resources:
            capabilities["resources"] = {}
        if declared.prompts:
            capabilities["prompts"] = {}
        if declared.logging:
            capabilities["logging"] = {}

        return {
            "protocolVersion": negotiated,
            "serverInfo": {
                "name": self.info.name,
                "version": self.info.version,
                "description": self.info.description,
            },
            "capabilities": capabilities,
        }

    async def _handle_set_level(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle logging/setLevel — mandatory once ``logging`` is advertised."""
        level = params.get("level")
        if level not in _LOG_LEVELS:
            raise ValueError(
                f"Invalid logging level: {level!r} (expected one of "
                f"{', '.join(_LOG_LEVELS)})"
            )
        self._log_level = str(level)
        logger.info("mcp_log_level_set", level=level)
        return {}

    async def _handle_list_tools(self) -> dict[str, Any]:
        """Handle tools/list request.

        Emits 2025-06-18 ``annotations`` (behavioural hints) derived from each
        tool's autonomy category so clients can gate side-effecting tools.
        """
        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "annotations": _tool_annotations(
                    getattr(tool, "category", "read_only")
                ),
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
        # SEP-1303 (2025-11-25): input-validation failures are *tool execution
        # errors* (isError: true) rather than JSON-RPC protocol errors, so the
        # calling model sees the message and can self-correct the arguments.
        schema = getattr(tool, "input_schema", None)
        if validator is not None:
            from jsonschema import ValidationError

            try:
                validator.validate(arguments)
            except ValidationError as exc:
                return self._tool_execution_error(
                    f"Invalid arguments for tool {tool_name}: {exc.message}"
                )
        elif isinstance(schema, dict) and schema:
            from jsonschema import ValidationError, validate

            try:
                validate(instance=arguments, schema=schema)
            except ValidationError as exc:
                return self._tool_execution_error(
                    f"Invalid arguments for tool {tool_name}: {exc.message}"
                )

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
        elif isinstance(result, dict) or isinstance(result, list):
            content = [{"type": "text", "text": json.dumps(result)}]
        else:
            content = [{"type": "text", "text": str(result)}]

        return {"content": content, "isError": False}

    @staticmethod
    def _tool_execution_error(message: str) -> dict[str, Any]:
        """A tools/call *result* carrying an execution error (SEP-1303)."""
        return {"content": [{"type": "text", "text": message}], "isError": True}

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
