"""Actions as tools: the audited action path and its agent/MCP bridges.

Every action goes through :func:`invoke_action`, which refuses actions the
spec does not declare and records who ran what (never the parameters, which
may carry personal data). The two bridges only adapt that single path:

* :func:`connector_tool_definitions` yields ``ToolDefinition`` objects for a
  ReAct agent's tool registry, named ``<connector>.<action>`` like the tools
  of a mounted MCP server;
* :func:`connector_mcp_tools` yields the dicts ``Plugin.get_mcp_tools()``
  returns, so a plugin exposes its connector on the core MCP server in one
  line.

Both carry the action's autonomy category, so the approval gate applies.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from core.connectors.errors import ConnectorConfigError, ConnectorError, redact
from core.connectors.protocols import Connector, SupportsAction
from core.connectors.types import ActionResult, ActionSpec
from core.observability.audit import AuditEventType, get_audit_logger

if TYPE_CHECKING:
    from core.reasoning.react_types import ToolDefinition

_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


async def _audit(
    connector: Connector, action: str, *, success: bool, error: str | None
) -> None:
    from core.context import get_current_user_id, get_tenant_or_default

    audit_logger = get_audit_logger()
    if not audit_logger.enabled:
        return
    await audit_logger.log(
        AuditEventType.TOOL_INVOKE,
        user_id=get_current_user_id(),
        tenant_id=get_tenant_or_default(),
        resource=f"connector:{connector.spec.name}",
        action=action,
        success=success,
        details={"error": error} if error else None,
    )


def _redacted(connector: Connector, text: str) -> str:
    """``text`` with the connector's secret credentials masked, if it has any."""
    values = getattr(connector, "secret_values", None)
    return redact(text, values() if callable(values) else ())


async def invoke_action(
    connector: Connector, action: str, params: Mapping[str, Any]
) -> ActionResult:
    """Run a declared action and audit it.

    The returned ``error`` is redacted: it is connector-written text and may
    quote a credential the vendor echoed back, and it reaches agents, MCP
    clients and the audit trail.

    Raises:
        ConnectorConfigError: The connector has no ``action`` capability or
            does not declare ``action``.
        ConnectorError: Whatever the connector raised, after auditing it.
    """
    name = connector.spec.name
    if not isinstance(connector, SupportsAction):
        raise ConnectorConfigError(name, "connector does not support actions")
    if connector.spec.action(action) is None:
        raise ConnectorConfigError(name, f"action {action!r} is not declared")
    try:
        result = await connector.invoke(action, params)
    except ConnectorError as exc:
        await _audit(
            connector, action, success=False, error=_redacted(connector, str(exc))
        )
        raise
    except Exception as exc:
        # A connector bug is still an attempted action: record it, but only
        # by type, since an arbitrary message may carry anything.
        await _audit(connector, action, success=False, error=type(exc).__name__)
        raise
    if result.error:
        result = ActionResult(
            ok=result.ok, data=result.data, error=_redacted(connector, result.error)
        )
    await _audit(connector, action, success=result.ok, error=result.error)
    return result


def _handler(connector: Connector, action: ActionSpec) -> Callable[..., Awaitable[str]]:
    async def call_connector_action(**arguments: Any) -> str:
        result = await invoke_action(connector, action.name, arguments)
        return json.dumps(asdict(result), default=str, ensure_ascii=False)

    call_connector_action.__name__ = f"{connector.spec.name}_{action.name}"
    return call_connector_action


def connector_tool_definitions(connector: Connector) -> list[ToolDefinition]:
    """One ``ToolDefinition`` per declared action, for an agent tool registry."""
    # Lazy import: core.reasoning pulls the whole reasoning stack.
    from core.reasoning.react_types import ToolDefinition

    name = connector.spec.name
    return [
        ToolDefinition(
            name=f"{name}.{action.name}",
            fn=_handler(connector, action),
            description=action.description,
            parameters=action.input_schema or None,
            category=action.category,
        )
        for action in connector.spec.actions
    ]


def connector_mcp_tools(connector: Connector) -> list[dict[str, Any]]:
    """Declared actions in the shape ``Plugin.get_mcp_tools()`` returns."""
    name = connector.spec.name
    return [
        {
            "name": f"{name}.{action.name}",
            "description": action.description,
            "input_schema": action.input_schema or dict(_EMPTY_SCHEMA),
            "handler": _handler(connector, action),
            "category": action.category,
        }
        for action in connector.spec.actions
    ]


__all__ = [
    "connector_mcp_tools",
    "connector_tool_definitions",
    "invoke_action",
]
