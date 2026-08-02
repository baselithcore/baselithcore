"""MCP Message Handlers.

Contains the JSON-RPC message routing and handling logic.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from core.mcp.errors import InvalidParams, MCPProtocolError, ResourceNotFound
from core.mcp.pagination import paginate
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
    _resource_templates: dict[str, Any]
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
                result = await self._handle_list_tools(params)
            elif method == "tools/call":
                result = await self._handle_call_tool(params)
            elif method == "resources/list":
                result = await self._handle_list_resources(params)
            elif method == "resources/templates/list":
                result = await self._handle_list_resource_templates(params)
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

        except MCPProtocolError as e:
            # A request the client can fix (unknown tool, bad cursor, missing
            # resource) — reported with the code the spec assigns it.
            logger.info("mcp_protocol_error", method=method, code=e.code, error=str(e))
            return self._error_response(msg_id, e.code, str(e))
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

    def _page(
        self, registry: dict[str, Any], params: dict[str, Any]
    ) -> tuple[list[Any], str | None]:
        """Slice *registry* into one page, ordered deterministically by key."""
        page_size = getattr(getattr(self, "config", None), "mcp_list_page_size", 100)
        keys, next_cursor = paginate(registry, params.get("cursor"), page_size)
        return [registry[key] for key in keys], next_cursor

    @staticmethod
    def _with_cursor(result: dict[str, Any], next_cursor: str | None) -> dict[str, Any]:
        """Attach ``nextCursor`` only when another page exists."""
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    async def _handle_list_tools(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/list request.

        Emits 2025-06-18 ``annotations`` (behavioural hints) derived from each
        tool's autonomy category so clients can gate side-effecting tools, plus
        ``outputSchema`` for tools that return structured content. Paginated
        through an opaque ``cursor``.
        """
        page, next_cursor = self._page(self._tools, params)
        tools = []
        for tool in page:
            entry: dict[str, Any] = {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "annotations": _tool_annotations(
                    getattr(tool, "category", "read_only")
                ),
            }
            output_schema = getattr(tool, "output_schema", None)
            if output_schema:
                entry["outputSchema"] = output_schema
            tools.append(entry)
        return self._with_cursor({"tools": tools}, next_cursor)

    async def _handle_call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/call request."""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name not in self._tools:
            raise InvalidParams(f"Unknown tool: {tool_name}")

        tool = self._tools[tool_name]
        if tool.handler is None:
            raise InvalidParams(f"Tool {tool_name} has no handler")

        if not isinstance(arguments, dict):
            raise InvalidParams(
                f"Invalid arguments for tool {tool_name}: expected object"
            )

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

        # Failures raised *by the tool* belong in the result, not in a JSON-RPC
        # error: the model needs to see them to retry or route around them.
        try:
            result = await tool.handler(**arguments)
        except Exception as exc:
            logger.warning(
                "mcp_tool_execution_failed", tool_name=tool_name, error=str(exc)
            )
            return self._tool_execution_error(f"Tool '{tool_name}' failed: {exc}")

        return self._format_tool_result(tool, result)

    def _format_tool_result(self, tool: Any, result: Any) -> dict[str, Any]:
        """Shape a handler's return value into a ``tools/call`` result.

        A tool declaring an ``outputSchema`` returns ``structuredContent``
        validated against it, mirrored as serialized JSON in a text block for
        clients that only read ``content`` (2025-06-18).
        """
        if getattr(tool, "output_schema", None):
            error = self._validate_tool_output(tool, result)
            if error is not None:
                return error
            return {
                "content": [{"type": "text", "text": json.dumps(result)}],
                "structuredContent": result,
                "isError": False,
            }

        if isinstance(result, str):
            content = [{"type": "text", "text": result}]
        elif isinstance(result, dict | list):
            content = [{"type": "text", "text": json.dumps(result)}]
        else:
            content = [{"type": "text", "text": str(result)}]

        return {"content": content, "isError": False}

    def _validate_tool_output(self, tool: Any, result: Any) -> dict[str, Any] | None:
        """Check *result* against the tool's declared output schema.

        Returns an error result when the contract is broken, else None. A
        declared schema is a promise to the client, so shipping a payload that
        violates it is worse than reporting the failure.
        """
        if not isinstance(result, dict):
            return self._tool_execution_error(
                f"Tool '{tool.name}' declares an output schema but returned "
                f"{type(result).__name__}, not an object"
            )
        validator = getattr(tool, "output_validator", None)
        if validator is None:
            return None
        from jsonschema import ValidationError

        try:
            validator.validate(result)
        except ValidationError as exc:
            logger.error(
                "mcp_tool_output_schema_violation", tool_name=tool.name, error=str(exc)
            )
            return self._tool_execution_error(
                f"Tool '{tool.name}' returned output violating its schema: "
                f"{exc.message}"
            )
        return None

    @staticmethod
    def _tool_execution_error(message: str) -> dict[str, Any]:
        """A tools/call *result* carrying an execution error (SEP-1303)."""
        return {"content": [{"type": "text", "text": message}], "isError": True}

    async def _handle_list_resources(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle resources/list request (concrete URIs only, paginated)."""
        page, next_cursor = self._page(self._resources, params)
        resources = [
            {
                "uri": res.uri,
                "name": res.name,
                "description": res.description,
                "mimeType": res.mime_type,
            }
            for res in page
        ]
        return self._with_cursor({"resources": resources}, next_cursor)

    async def _handle_list_resource_templates(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle resources/templates/list — the parameterized resources."""
        page, next_cursor = self._page(self._resource_templates, params)
        templates = [
            {
                "uriTemplate": template.uri_template,
                "name": template.name,
                "description": template.description,
                "mimeType": template.mime_type,
            }
            for template in page
        ]
        return self._with_cursor({"resourceTemplates": templates}, next_cursor)

    def _resolve_resource(self, uri: str) -> tuple[Any, dict[str, str]]:
        """Find the resource or template serving *uri*.

        Returns:
            ``(resource_or_template, variables)`` — variables is empty for a
            concrete resource.

        Raises:
            ResourceNotFound: Nothing registered serves the URI.
        """
        from core.mcp.uri_template import match_template

        resource = self._resources.get(uri)
        if resource is not None:
            return resource, {}

        for template in self._resource_templates.values():
            variables = match_template(template.pattern, uri)
            if variables is not None:
                return template, variables

        raise ResourceNotFound(f"Unknown resource: {uri}")

    async def _handle_read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle resources/read for a concrete URI or a templated one."""
        uri = params.get("uri", "")
        resource, variables = self._resolve_resource(uri)
        if resource.handler is None:
            raise ResourceNotFound(f"Resource {uri} has no read handler")

        logger.info(f"MCP resource read: uri={uri}")

        content = await resource.handler(uri, **variables)

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
