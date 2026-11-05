"""Expose catalog workflows as MCP tools.

A workflow becomes a tool only when its author asks for it::

    metadata:
      mcp:
        expose: true
        category: mutating          # autonomy category; default "destructive"
        description: Summarise a support ticket
        input_schema: {type: object, properties: {...}}   # optional

The default category is ``destructive`` so an exposed workflow stays behind
the server's approval gate until its author states what it actually does -
the same fail-safe default plugin tools get.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from baselith.core.observability.logging import get_logger

from .builder import WorkflowDefinition
from .runtime import AWAITING_APPROVAL, WorkflowRuntime

if TYPE_CHECKING:
    from baselith.core.mcp.server import MCPServer

logger = get_logger(__name__)

__all__ = ["tool_name_for", "unregister_workflow_tool", "register_workflow_tool"]

_CATEGORIES = frozenset(
    {"read_only", "mutating", "destructive", "external_side_effect"}
)
_ANY_JSON = {"type": ["string", "number", "boolean", "object", "array", "null"]}
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def tool_name_for(workflow_id: str) -> str:
    """The MCP tool name of a workflow (``workflow.<id>``, at most 128 chars)."""
    return f"workflow.{_UNSAFE.sub('_', workflow_id)}"[:128]


def _settings(workflow: WorkflowDefinition) -> dict[str, Any] | None:
    mcp = workflow.metadata.get("mcp")
    if isinstance(mcp, dict) and mcp.get("expose") is True:
        return mcp
    return None


def unregister_workflow_tool(server: MCPServer, workflow_id: str) -> bool:
    """Withdraw a workflow's tool; returns whether one was registered."""
    return bool(server.unregister_tool(tool_name_for(workflow_id)))


def register_workflow_tool(
    server: MCPServer, workflow: WorkflowDefinition, runtime: WorkflowRuntime
) -> str | None:
    """(Re)register one workflow's tool, or withdraw it if no longer exposed.

    Returns:
        The tool name when registered, ``None`` otherwise.
    """
    unregister_workflow_tool(server, workflow.id)
    settings = _settings(workflow)
    if settings is None:
        return None
    category = settings.get("category", "destructive")
    if category not in _CATEGORIES:
        logger.warning(
            "workflow_mcp_category_invalid id=%s category=%r; using 'destructive'",
            workflow.id,
            category,
        )
        category = "destructive"
    schema = settings.get("input_schema")
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {"input": _ANY_JSON}}
    workflow_id = workflow.id

    async def _call(**arguments: Any) -> dict[str, Any]:
        # The whole argument object is the workflow input when the author
        # supplied a schema of their own; with the default schema it is the
        # single ``input`` property.
        payload = arguments if settings.get("input_schema") else arguments.get("input")
        record = await runtime.run(workflow_id, payload, source="mcp")
        return {
            "run_id": record.run_id,
            "status": record.status,
            "output": record.output,
            "error": record.error,
            "awaiting_approval": record.status == AWAITING_APPROVAL,
        }

    name = tool_name_for(workflow.id)
    server.register_tool(
        name=name,
        description=str(
            settings.get("description") or workflow.description or workflow.name
        ),
        input_schema=schema,
        handler=_call,
        category=category,
    )
    return name
