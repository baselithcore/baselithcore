"""Static validation of the data and model nodes' configuration.

Run from :meth:`WorkflowDefinition.validate`, so a definition is rejected when
it is registered, validated from the CLI, or uploaded through the API - not
half-way through a run after earlier nodes already had side effects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._scope import RESERVED_NAMES
from .builder import NodeType, WorkflowDefinition, WorkflowNode

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["MAX_NESTING_DEPTH", "validate_node_configs"]

#: How deep nested definitions (ITERATION, SUBGRAPH) are validated.
MAX_NESTING_DEPTH = 5

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_DEFAULT_LABELS = ("default", "else")


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_llm(node: WorkflowNode, wf: WorkflowDefinition, depth: int) -> list[str]:
    if not _nonempty_str(node.config.get("prompt")):
        return [f"LLM node {node.id} needs a config['prompt']"]
    return []


def _check_template(
    node: WorkflowNode, wf: WorkflowDefinition, depth: int
) -> list[str]:
    if not _nonempty_str(node.config.get("template")):
        return [f"TEMPLATE node {node.id} needs a config['template']"]
    return []


def _check_http(node: WorkflowNode, wf: WorkflowDefinition, depth: int) -> list[str]:
    errors = []
    if not _nonempty_str(node.config.get("url")):
        errors.append(f"HTTP node {node.id} needs a config['url']")
    method = str(node.config.get("method", "GET")).upper()
    if method not in _HTTP_METHODS:
        errors.append(f"HTTP node {node.id} has unsupported method {method!r}")
    return errors


def _check_assign(node: WorkflowNode, wf: WorkflowDefinition, depth: int) -> list[str]:
    config = node.config
    errors = []
    assign = config.get("assign")
    coalesce = config.get("coalesce")
    if not assign and not coalesce:
        return [f"ASSIGN node {node.id} assigns nothing"]
    names = list(assign or {})
    if coalesce:
        names.append(coalesce.get("into", ""))
    for name in names:
        if name in RESERVED_NAMES:
            errors.append(f"ASSIGN node {node.id}: {name!r} is a reserved name")
    return errors


def _nested(node: WorkflowNode, key: str, depth: int, kind: str) -> list[str]:
    sub = node.config.get(key)
    if sub is None:
        return [f"{kind} node {node.id} needs config[{key!r}]"]
    if depth >= MAX_NESTING_DEPTH:
        return [f"{kind} node {node.id}: nesting deeper than {MAX_NESTING_DEPTH}"]
    try:
        definition = WorkflowDefinition.from_dict(sub) if isinstance(sub, dict) else sub
        if not isinstance(definition, WorkflowDefinition):
            return [f"{kind} node {node.id}: config[{key!r}] is not a workflow"]
        nested_errors = [
            *definition.structure_errors(),
            *validate_node_configs(definition, depth + 1),
        ]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return [f"{kind} node {node.id}: invalid nested workflow ({exc})"]
    return [f"{kind} node {node.id} -> {err}" for err in nested_errors]


def _check_iteration(
    node: WorkflowNode, wf: WorkflowDefinition, depth: int
) -> list[str]:
    return _nested(node, "workflow", depth, "ITERATION")


def _check_classifier(
    node: WorkflowNode, wf: WorkflowDefinition, depth: int
) -> list[str]:
    raw = node.config.get("categories")
    if not isinstance(raw, list) or len(raw) < 2:
        return [f"CLASSIFIER node {node.id} needs at least two categories"]
    labels = [c if isinstance(c, str) else (c or {}).get("label") for c in raw]
    if not all(_nonempty_str(label) for label in labels):
        return [f"CLASSIFIER node {node.id} has a category without a label"]
    if len(set(labels)) != len(labels):
        return [f"CLASSIFIER node {node.id} has duplicate category labels"]
    errors = []
    edges = wf.get_outgoing_edges(node.id)
    edge_labels = {e.condition_label for e in edges}
    if None in edge_labels:
        errors.append(f"CLASSIFIER node {node.id}: every outgoing edge needs a label")
    has_catch_all = any(label in edge_labels for label in _DEFAULT_LABELS)
    for label in labels:
        if label not in edge_labels and not has_catch_all:
            errors.append(
                f"CLASSIFIER node {node.id}: no edge for class {label!r} "
                "and no 'default' edge"
            )
    return errors


def _check_list(node: WorkflowNode, wf: WorkflowDefinition, depth: int) -> list[str]:
    from .nodes_list import LIST_OPERATIONS

    steps = node.config.get("ops")
    if not isinstance(steps, list) or not steps:
        return [f"LIST node {node.id} needs a non-empty config['ops']"]
    errors = []
    for position, step in enumerate(steps):
        if not isinstance(step, dict) or len(step) != 1:
            errors.append(
                f"LIST node {node.id}: step {position} must be a one-key mapping"
            )
        elif next(iter(step)) not in LIST_OPERATIONS:
            errors.append(
                f"LIST node {node.id}: unknown step {next(iter(step))!r} "
                f"(use {', '.join(LIST_OPERATIONS)})"
            )
    return errors


def _check_code(node: WorkflowNode, wf: WorkflowDefinition, depth: int) -> list[str]:
    errors = []
    if not _nonempty_str(node.config.get("code")):
        errors.append(f"CODE node {node.id} needs a config['code']")
    if str(node.config.get("language", "python")).lower() != "python":
        errors.append(f"CODE node {node.id}: only python is supported")
    return errors


_CHECKS: dict[
    NodeType, Callable[[WorkflowNode, WorkflowDefinition, int], list[str]]
] = {
    NodeType.LLM: _check_llm,
    NodeType.TEMPLATE: _check_template,
    NodeType.HTTP: _check_http,
    NodeType.ASSIGN: _check_assign,
    NodeType.ITERATION: _check_iteration,
    NodeType.CLASSIFIER: _check_classifier,
    NodeType.LIST: _check_list,
    NodeType.CODE: _check_code,
}


def validate_node_configs(workflow: WorkflowDefinition, depth: int = 0) -> list[str]:
    """Collect configuration errors of the typed nodes of ``workflow``.

    Args:
        workflow: The definition to check.
        depth: Current nesting level (internal; nested definitions recurse).

    Returns:
        Human-readable errors, empty when every typed node is well formed.
    """
    errors: list[str] = []
    for node in workflow.nodes:
        check = _CHECKS.get(node.type)
        if check is not None:
            errors.extend(check(node, workflow, depth))
    return errors
