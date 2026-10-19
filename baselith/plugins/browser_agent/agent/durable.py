"""Durability of a browser task inside a checkpointed run.

A browser task cannot be resumed mid-way. Its state lives in the browser — the current page,
cookies, half-filled forms — and a resumed run starts with a fresh browser, so replaying the
recorded decisions of the first steps would leave the page where it started while the model
believed it had moved on, and re-executing them would repeat whatever a click or a submit did.
Neither is sound, so the unit of durability is the **whole task**:

* the task's outcome is one checkpoint step: a resumed run whose browser task had finished
  replays its result (without the screenshots) and never relaunches the browser;
* before every action with a side effect (``click``, ``type``) a write-ahead *effect marker* is
  recorded, and an attempt marker is recorded when the task starts;
* a resumed run that finds the task started but unfinished re-runs it from scratch only when no
  effect marker was recorded (navigation, scrolling and reading are safe to repeat); otherwise it
  refuses with a failed result rather than risk acting twice — at-most-once for side effects.

Without a checkpoint nothing changes.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from baselith.core.orchestration.checkpoint_scope import current_checkpoint

from ..types import BrowserAction, BrowserActionType, BrowserAgentResult

#: Actions whose effect on a page may be visible outside the browser.
EFFECT_ACTIONS = frozenset({BrowserActionType.CLICK, BrowserActionType.TYPE})

INTERRUPTED_ERROR = (
    "the browser task was interrupted after it acted on a page (click or type); "
    "it is not re-run automatically, so an action is never performed twice"
)

_task_scope: ContextVar[Any | None] = ContextVar("browser_task_scope", default=None)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def result_to_dict(result: BrowserAgentResult) -> dict[str, Any]:
    """A JSON-safe copy of ``result``; screenshots are not persisted (size)."""
    return {
        "success": result.success,
        "final_url": result.final_url,
        "steps_taken": result.steps_taken,
        "extracted_data": dict(result.extracted_data),
        "error": result.error,
    }


def result_from_dict(data: dict[str, Any]) -> BrowserAgentResult:
    """The result a recorded step stored (``screenshots`` is empty)."""
    return BrowserAgentResult(
        success=bool(data.get("success")),
        final_url=str(data.get("final_url") or ""),
        steps_taken=int(data.get("steps_taken") or 0),
        extracted_data=dict(data.get("extracted_data") or {}),
        error=data.get("error"),
    )


@contextmanager
def _bound(scope: Any) -> Iterator[None]:
    token = _task_scope.set(scope)
    try:
        yield
    finally:
        _task_scope.reset(token)


def _effects_recorded(scope: Any) -> bool:
    name = f"{scope.path}/effect"
    steps = scope.checkpoint.steps.values()
    return any(entry.get("tool_name") == name for entry in steps)


async def mark_effect(step: int, action: BrowserAction) -> None:
    """Record a write-ahead marker before a side-effecting action runs (no-op otherwise)."""
    scope = _task_scope.get()
    if scope is None or action.action_type not in EFFECT_ACTIONS:
        return
    marker = {
        "step": step,
        "action": action.action_type.value,
        "selector": action.selector,
        "value": _digest(action.value or ""),
    }

    async def _mark() -> dict[str, Any]:
        return marker

    await scope.run_step("effect", marker, _mark, category="external_side_effect")


async def durable_task(
    task: str,
    max_steps: int,
    run: Callable[[str], Awaitable[BrowserAgentResult]],
) -> BrowserAgentResult:
    """Run ``run(task)`` as one recorded step when a checkpoint is in context."""
    checkpoint = current_checkpoint()
    if checkpoint is None:
        return await run(task)
    scope = checkpoint.scope(
        "browser_agent", {"task": _digest(task), "max_steps": max_steps}
    )

    async def _whole() -> dict[str, Any]:
        token = uuid.uuid4().hex

        async def _attempt() -> dict[str, Any]:
            return {"token": token}

        marker = await scope.run_step("attempt", {}, _attempt, category="browser")
        if marker.get("token") != token and _effects_recorded(scope):
            return {
                "success": False,
                "final_url": "",
                "steps_taken": 0,
                "extracted_data": {},
                "error": INTERRUPTED_ERROR,
            }
        with _bound(scope):
            return result_to_dict(await run(task))

    recorded = await scope.run_step(
        "task", {"task": _digest(task)}, _whole, category="browser"
    )
    return result_from_dict(recorded)


__all__ = [
    "EFFECT_ACTIONS",
    "INTERRUPTED_ERROR",
    "durable_task",
    "mark_effect",
    "result_from_dict",
    "result_to_dict",
]
