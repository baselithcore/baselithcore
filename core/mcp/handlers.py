"""MCP Message Handlers.

Contains the JSON-RPC message routing and handling logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.mcp.completion import CompletionHandlerMixin
from core.mcp.errors import MCPProtocolError
from core.mcp.modern import (
    MODERN_PROTOCOL_VERSION,
    REMOVED_IN_MODERN,
    finalize_result,
    is_modern,
    parse_request_meta,
    request_meta,
)
from core.mcp.prompt_handlers import PromptHandlerMixin
from core.mcp.resource_handlers import ResourceHandlerMixin
from core.mcp.subscriptions import SubscriptionHandlerMixin
from core.mcp.tasks import EXTENSION_ID as TASKS_EXTENSION_ID
from core.mcp.tool_handlers import ToolHandlerMixin
from core.observability.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# MCP protocol versions this server speaks, newest first. 2026-07-28 is the
# *modern* revision: stateless, no handshake, per-request `_meta`. The rest are
# *legacy* revisions selected by an `initialize` handshake, which the server
# still answers for older clients (see core.mcp.modern for the era split).
# 2025-11-25 adds Implementation.description, icons metadata and the SEP-1303
# rule (input-validation failures are tool execution errors, not protocol
# errors); 2025-06-18 adds tool annotations (behavioural hints) and structured
# tool output; 2024-11-05 is retained for backward compatibility.
LATEST_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = (
    MODERN_PROTOCOL_VERSION,
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


class MessageHandlerMixin(
    ToolHandlerMixin,
    SubscriptionHandlerMixin,
    ResourceHandlerMixin,
    PromptHandlerMixin,
    CompletionHandlerMixin,
):
    """Mixin providing MCP message handling functionality.

    Handles JSON-RPC message routing for MCP protocol methods; the per-
    primitive handlers live in the mixins it composes.
    """

    # These will be provided by the main class
    info: Any
    config: Any
    _tools: dict[str, Any]
    _resources: dict[str, Any]
    _resource_templates: dict[str, Any]
    _prompts: dict[str, Any]
    _autonomy_policy: Any
    # Minimum severity the client asked to receive (logging/setLevel).
    _log_level: str = "info"

    async def handle_message(
        self, message: dict[str, Any], send: Any | None = None
    ) -> dict[str, Any] | None:
        """
        Handle an incoming MCP JSON-RPC message.

        Args:
            message: Parsed JSON-RPC message
            send: Coroutine writing a message back on this request's stream.
                Required by ``subscriptions/listen``, which *is* a stream; the
                other methods never need it.

        Returns:
            Response message or None for notifications
        """
        method = message.get("method", "")
        params = message.get("params", {})
        msg_id = message.get("id")
        modern = is_modern(message)

        logger.debug(f"MCP message received: method={method}, id={msg_id}")

        meta_token = None
        try:
            if modern:
                # 2026-07-28 carries version, identity and capabilities on every
                # request; validating here keeps the handlers era-agnostic.
                meta_token = request_meta.set(
                    parse_request_meta(message, SUPPORTED_PROTOCOL_VERSIONS)
                )
                if method in REMOVED_IN_MODERN:
                    return self._error_response(
                        msg_id, -32601, f"Method not found: {method}"
                    )

            # Route to appropriate handler
            if method == "server/discover":
                result = await self._handle_discover()
            elif method == "initialize":
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
            elif method == "prompts/list":
                result = await self._handle_list_prompts(params)
            elif method == "prompts/get":
                result = await self._handle_get_prompt(params)
            elif method == "completion/complete":
                result = await self._handle_complete(params)
            elif method.startswith("tasks/") and not modern:
                # The tasks extension exists only in the modern revision.
                return self._error_response(
                    msg_id, -32601, f"Method not found: {method}"
                )
            elif method == "subscriptions/listen":
                if send is None:
                    # Nothing to deliver on: better an explicit error than a
                    # subscription that silently drops every notification.
                    return self._error_response(
                        msg_id,
                        -32601,
                        "subscriptions/listen requires a streaming transport",
                    )
                result = await self._handle_listen(params, msg_id, send)
            elif method == "tasks/get":
                result = await self._handle_task_get(params)
            elif method == "tasks/update":
                result = await self._handle_task_update(params)
            elif method == "tasks/cancel":
                result = await self._handle_task_cancel(params)
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

            # `server/discover` exists only in the modern revision, so its
            # result always takes the modern shape — including when a client
            # sends it bare as the stdio era probe.
            if modern or method == "server/discover":
                result = finalize_result(
                    result,
                    method,
                    {"name": self.info.name, "version": self.info.version},
                    ttl_ms=getattr(self.config, "mcp_cache_ttl_ms", 60000),
                    cache_scope=getattr(self.config, "mcp_cache_scope", "private"),
                )
            return self._success_response(msg_id, result)

        except MCPProtocolError as e:
            # A request the client can fix (unknown tool, bad cursor, missing
            # resource) — reported with the code the spec assigns it, which
            # some revisions renumbered.
            code = e.code_for(modern)
            logger.info("mcp_protocol_error", method=method, code=code, error=str(e))
            return self._error_response(msg_id, code, str(e), data=e.data)
        except Exception as e:
            logger.exception(f"MCP handler error: method={method}, error={e}")
            return self._error_response(msg_id, -32603, str(e))
        finally:
            if meta_token is not None:
                request_meta.reset(meta_token)

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

        return {
            "protocolVersion": negotiated,
            "serverInfo": {
                "name": self.info.name,
                "version": self.info.version,
                "description": self.info.description,
            },
            "capabilities": self._capabilities(),
        }

    def _capabilities(self) -> dict[str, Any]:
        """Build the advertised `ServerCapabilities`.

        Members are objects or absent — never JSON null, which strictly-typed
        clients reject. Sub-capabilities are advertised only when actually
        implemented.
        """
        declared = self.info.capabilities
        capabilities: dict[str, Any] = {}
        # `listChanged` is advertised because the server really does emit the
        # notifications, on any subscriptions/listen stream that opted in.
        if declared.tools:
            capabilities["tools"] = {"listChanged": True}
        if declared.resources:
            capabilities["resources"] = {"listChanged": True}
        # Prompts and completions follow what is actually registered: neither
        # is a static server trait the way tools/resources support is.
        if declared.prompts or self._prompts:
            capabilities["prompts"] = {"listChanged": True}
        if self._has_completions():
            capabilities["completions"] = {}
        if declared.logging:
            capabilities["logging"] = {}
        # Extensions this server implements; a client opts in per request.
        capabilities["extensions"] = {TASKS_EXTENSION_ID: {}}
        return capabilities

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

    def _success_response(self, msg_id: Any, result: Any) -> dict[str, Any]:
        """Create a success response."""
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": result,
        }

    def _error_response(
        self, msg_id: Any, code: int, message: str, data: Any | None = None
    ) -> dict[str, Any]:
        """Create an error response."""
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": msg_id, "error": error}

    async def _handle_discover(self) -> dict[str, Any]:
        """Handle server/discover — mandatory from revision 2026-07-28.

        Returns the versions, capabilities and identity a client would
        otherwise have learned from the retired ``initialize`` handshake, in a
        single cacheable request.
        """
        result: dict[str, Any] = {
            "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "capabilities": self._capabilities(),
        }
        instructions = getattr(self.config, "mcp_server_instructions", "")
        if instructions:
            result["instructions"] = instructions
        return result
