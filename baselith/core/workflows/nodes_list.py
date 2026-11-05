"""LIST node: reshape a list without code.

Filter, sort, slice, de-duplicate, project and flatten a list in a declared
sequence of steps::

    - id: best
      type: list
      config:
        items: "{{ nodes.find }}"
        ops:
          - filter: "item['score'] > 0.5"
          - sort: {key: score, descending: true}
          - unique: document_id
          - limit: 3
          - pluck: content

A step never runs code: ``filter`` uses the same whitelisted expression
evaluator as CONDITION nodes (``item`` is the element, ``index`` its position).
"""

from __future__ import annotations

import json
from typing import Any

from ._scope import TemplateError, build_scope, render_value, resolve_path
from .builder import WorkflowNode
from .conditions import _safe_condition

__all__ = ["LIST_OPERATIONS", "MAX_LIST_ITEMS", "handle_list"]

#: The step names a LIST node understands, in the order they are documented.
LIST_OPERATIONS = (
    "filter",
    "sort",
    "reverse",
    "unique",
    "offset",
    "limit",
    "pluck",
    "flatten",
)

#: Refuse lists longer than this: every step walks the whole list.
MAX_LIST_ITEMS = 100_000


def _value(item: Any, key: str | None, node: WorkflowNode, op: str) -> Any:
    """``item`` itself, or the value at ``key`` (dotted path) inside it."""
    if key is None:
        return item
    path = f"i{key}" if key.startswith("[") else f"i.{key}"
    try:
        return resolve_path({"i": item}, path)
    except TemplateError as exc:
        raise ValueError(
            f"LIST node {node.id!r} ({op}): no {key!r} in an item"
        ) from exc


def _count(node: WorkflowNode, op: str, raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ValueError(f"LIST node {node.id!r}: {op} needs a non-negative integer")
    return raw


def _identity(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _apply(node: WorkflowNode, op: str, arg: Any, items: list[Any]) -> list[Any]:
    if op == "filter":
        if not isinstance(arg, str) or not arg.strip():
            raise ValueError(f"LIST node {node.id!r}: filter needs an expression")
        kept = []
        for index, item in enumerate(items):
            try:
                if _safe_condition(arg, {"item": item, "index": index}):
                    kept.append(item)
            except Exception as exc:
                raise ValueError(
                    f"LIST node {node.id!r}: filter failed on item {index}: {exc}"
                ) from exc
        return kept
    if op == "sort":
        spec = {"key": arg} if isinstance(arg, str) else dict(arg or {})
        key = spec.get("key")
        try:
            return sorted(
                items,
                key=lambda item: _value(item, key, node, op),
                reverse=bool(spec.get("descending", False)),
            )
        except TypeError as exc:
            raise ValueError(
                f"LIST node {node.id!r}: sort values are not comparable"
            ) from exc
    if op == "reverse":
        return items[::-1] if arg else items
    if op == "unique":
        key = arg if isinstance(arg, str) else None
        seen: set[str] = set()
        out = []
        for item in items:
            ident = _identity(_value(item, key, node, op))
            if ident not in seen:
                seen.add(ident)
                out.append(item)
        return out
    if op == "offset":
        return items[_count(node, op, arg) :]
    if op == "limit":
        return items[: _count(node, op, arg)]
    if op == "pluck":
        if not isinstance(arg, str) or not arg:
            raise ValueError(f"LIST node {node.id!r}: pluck needs a key")
        return [_value(item, arg, node, op) for item in items]
    if op == "flatten":
        flat: list[Any] = []
        for item in items:
            if arg and isinstance(item, list):
                flat.extend(item)
            else:
                flat.append(item)
        return flat
    raise ValueError(f"LIST node {node.id!r}: unknown step {op!r}")


def handle_list(node: WorkflowNode, context: Any) -> list[Any]:
    """Run the node's ``ops`` over ``config['items']`` (default ``{{ output }}``)."""
    items = render_value(node.config.get("items", "{{ output }}"), build_scope(context))
    if not isinstance(items, list):
        raise ValueError(f"LIST node {node.id!r}: items must resolve to a list")
    if len(items) > MAX_LIST_ITEMS:
        raise ValueError(f"LIST node {node.id!r}: more than {MAX_LIST_ITEMS} items")
    steps = node.config.get("ops")
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"LIST node {node.id!r} needs a non-empty config['ops']")
    result = list(items)
    for step in steps:
        if not isinstance(step, dict) or len(step) != 1:
            raise ValueError(f"LIST node {node.id!r}: each step is a one-key mapping")
        ((op, arg),) = step.items()
        result = _apply(node, op, arg, result)
    return result
