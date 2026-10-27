"""Data-shaping node handlers: TEMPLATE, ASSIGN and ITERATION.

These nodes only move data around inside a run. They call nothing external,
so a workflow built from them is deterministic and needs no registry on the
executor.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

from ._scope import RESERVED_NAMES, TemplateError, build_scope, render_value
from ._scope import resolve_path as _resolve
from .builder import WorkflowDefinition, WorkflowNode
from .durable import NODE_CHECKPOINT_KEY

if TYPE_CHECKING:
    from .executor import ExecutionContext, WorkflowExecutor

__all__ = [
    "MAX_ITERATION_ITEMS",
    "handle_assign",
    "handle_iteration",
    "handle_template",
]

#: Hard ceiling for ``config['max_items']`` on an ITERATION node.
MAX_ITERATION_ITEMS = 10_000
_DEFAULT_ITERATION_ITEMS = 500
_MAX_CONCURRENCY = 16
_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def handle_template(node: WorkflowNode, context: ExecutionContext) -> Any:
    """Render ``config['template']`` against the run scope.

    A template that is exactly one placeholder returns the referenced object
    unchanged; anything else returns the rendered string.
    """
    template = node.config.get("template")
    if not isinstance(template, str) or not template:
        raise ValueError(f"TEMPLATE node {node.id!r} needs a config['template'] string")
    return render_value(template, build_scope(context))


def handle_assign(node: WorkflowNode, context: ExecutionContext) -> Any:
    """Set run variables from ``config['assign']`` and ``config['coalesce']``.

    ``assign`` maps a variable name to a value or template, all rendered
    against the scope as it was before the node ran (so the order of the
    entries does not matter). ``coalesce`` is ``{"into": name, "paths":
    [...]}`` and stores the first path that resolves to a non-empty value.

    The node passes the previous output through unchanged; later nodes read
    the variables with ``{{ name }}``.
    """
    config = node.config
    scope = build_scope(context)
    updates: dict[str, Any] = {}

    for name, value in (config.get("assign") or {}).items():
        updates[_checked_name(node, name)] = render_value(value, scope)

    coalesce = config.get("coalesce")
    if coalesce:
        target = _checked_name(node, coalesce.get("into", ""))
        for path in coalesce.get("paths") or []:
            try:
                found = _resolve(scope, path)
            except TemplateError:
                continue
            if found is not None and found != "":
                updates[target] = found
                break
        else:
            raise ValueError(
                f"ASSIGN node {node.id!r}: none of the coalesce paths resolved"
            )

    if not updates:
        raise ValueError(f"ASSIGN node {node.id!r} assigns nothing")
    for name, value in updates.items():
        context.set_variable(name, value)
    return context.get_last_output()


def _checked_name(node: WorkflowNode, name: Any) -> str:
    if not isinstance(name, str) or not _VARIABLE_NAME.match(name):
        raise ValueError(f"ASSIGN node {node.id!r}: invalid variable name {name!r}")
    if name in RESERVED_NAMES:
        raise ValueError(f"ASSIGN node {node.id!r}: {name!r} is a reserved name")
    return name


def _nested_workflow(node: WorkflowNode) -> WorkflowDefinition:
    sub = node.config.get("workflow")
    if isinstance(sub, dict):
        sub = WorkflowDefinition.from_dict(sub)
    if not isinstance(sub, WorkflowDefinition):
        raise ValueError(f"ITERATION node {node.id!r} needs config['workflow']")
    return sub


async def handle_iteration(
    executor: WorkflowExecutor, node: WorkflowNode, context: ExecutionContext
) -> Any:
    """Run a nested workflow once per item of a list.

    Config:
        items: Template resolving to a list; defaults to ``{{ output }}``.
        workflow: The nested definition (or its ``to_dict()``). Each item is
            its initial input.
        concurrency: Items in flight at once (1-16, default 1).
        max_items: Refuse longer lists (default 500, ceiling 10000).
        on_error: ``"fail"`` (default) stops at the first failed item;
            ``"continue"`` records ``{"error": ...}`` for it and moves on.

    Returns:
        The nested outputs, in item order.

    In durable mode the items run one at a time and each gets its own child
    scope of the node visit, so a resumed run replays the finished items.
    """
    from .executor import ExecutionStatus

    config = node.config
    items = render_value(config.get("items", "{{ output }}"), build_scope(context))
    if not isinstance(items, list):
        raise ValueError(f"ITERATION node {node.id!r}: items must resolve to a list")
    cap = min(
        int(config.get("max_items", _DEFAULT_ITERATION_ITEMS)), MAX_ITERATION_ITEMS
    )
    if len(items) > cap:
        raise ValueError(
            f"ITERATION node {node.id!r}: {len(items)} items exceed max_items={cap}"
        )
    on_error = config.get("on_error", "fail")
    if on_error not in ("fail", "continue"):
        raise ValueError(f"ITERATION node {node.id!r}: on_error must be fail|continue")
    sub = _nested_workflow(node)

    node_scope = context.__dict__.get(NODE_CHECKPOINT_KEY)
    make_scope = getattr(node_scope, "scope", None)
    concurrency = 1 if make_scope is not None else _concurrency(config)
    gate = asyncio.Semaphore(concurrency)

    async def _one(index: int, item: Any) -> Any:
        async with gate:
            scope = (
                make_scope("iteration_item", {"node_id": node.id, "index": index})
                if make_scope is not None
                else None
            )
            result = await executor.execute(sub, initial_input=item, checkpoint=scope)
        if result.status is ExecutionStatus.COMPLETED:
            return result.output
        message = f"item {index} failed: {result.error or result.status.value}"
        if on_error == "continue":
            return {"error": message}
        raise RuntimeError(f"ITERATION node {node.id!r}: {message}")

    if concurrency == 1:
        return [await _one(i, item) for i, item in enumerate(items)]
    tasks = [asyncio.ensure_future(_one(i, item)) for i, item in enumerate(items)]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        # One failed item must not leave its siblings running unobserved.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _concurrency(config: dict[str, Any]) -> int:
    return max(1, min(int(config.get("concurrency", 1)), _MAX_CONCURRENCY))
