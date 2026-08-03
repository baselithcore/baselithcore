"""``tools/list`` and ``tools/call`` handlers.

Tools are the model-facing primitive, so this is where the protocol's
error taxonomy matters most: input-validation failures and handler exceptions
are *tool execution errors* the model can act on, while unknown tools and
malformed envelopes are protocol errors it cannot. A handler that needs the
user or the client's model to supply something raises
:class:`~core.mcp.mrtr.InputRequired` and the call becomes a multi round-trip
request instead of failing.
"""

from __future__ import annotations

import json
from typing import Any

from core.mcp.errors import InvalidParams
from core.mcp.mrtr import (
    InputRequired,
    LegacyInputUnsupported,
    RoundTripMixin,
    input_responses,
    request_state,
)
from core.mcp.pagination import page_registry, with_cursor
from core.mcp.tasks import TaskHandlerMixin
from core.observability.logging import get_logger

logger = get_logger(__name__)


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


class ToolHandlerMixin(RoundTripMixin, TaskHandlerMixin):
    """Mixin serving the tools primitive."""

    info: Any
    config: Any
    _tools: dict[str, Any]
    _autonomy_policy: Any
    _state_sealer: Any

    async def _handle_list_tools(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/list request.

        Emits 2025-06-18 ``annotations`` (behavioural hints) derived from each
        tool's autonomy category so clients can gate side-effecting tools, plus
        ``outputSchema`` for tools that return structured content. Paginated
        through an opaque ``cursor``.
        """
        page, next_cursor = page_registry(self._tools, params, self.config)
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
            if tool.output_schema:
                entry["outputSchema"] = tool.output_schema
            if tool.icons:
                entry["icons"] = tool.icons
            tools.append(entry)
        return with_cursor({"tools": tools}, next_cursor)

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

        if tool.long_running and self.client_wants_tasks():
            # The client asked to be handed a durable handle rather than have
            # its connection held open for the duration.
            return await self._run_as_task(
                self._task_runner(tool, arguments),
                ttl_ms=getattr(self.config, "mcp_task_ttl_ms", 3_600_000),
                poll_interval_ms=getattr(
                    self.config, "mcp_task_poll_interval_ms", 1000
                ),
            )

        # Failures raised *by the tool* belong in the result, not in a JSON-RPC
        # error: the model needs to see them to retry or route around them.
        tokens = self._enter_round_trip(params, "tools/call")
        try:
            result = await tool.handler(**arguments)
        except InputRequired as exc:
            try:
                return self._input_required(exc, "tools/call")
            except LegacyInputUnsupported as legacy:
                # The revision the client speaks has no way to carry the ask.
                return self._tool_execution_error(str(legacy))
        except Exception as exc:
            logger.warning(
                "mcp_tool_execution_failed", tool_name=tool_name, error=str(exc)
            )
            return self._tool_execution_error(f"Tool '{tool_name}' failed: {exc}")
        finally:
            self._exit_round_trip(tokens)

        return self._format_tool_result(tool, result)

    def _task_runner(self, tool: Any, arguments: dict[str, Any]) -> Any:
        """Build the coroutine a task drives, answers injected on each round.

        The answers arrive through ``tasks/update`` rather than a retried
        request, so they are bound here instead of by ``_enter_round_trip``.
        """

        async def run(answers: dict[str, Any]) -> dict[str, Any]:
            tokens = (input_responses.set(answers), request_state.set(None))
            try:
                result = await tool.handler(**arguments)
            except InputRequired:
                raise
            except Exception as exc:
                return self._tool_execution_error(f"Tool '{tool.name}' failed: {exc}")
            finally:
                self._exit_round_trip(tokens)
            return self._format_tool_result(tool, result)

        return run

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
