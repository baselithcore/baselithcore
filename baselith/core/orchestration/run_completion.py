"""Tell a run's originator that the recovery sweep finished it.

A run started by a queued job reports its outcome through the job: the
TaskTracker status a client polls and the terminal webhook it subscribes to.
When the worker dies mid-run, the recovery sweep of some API replica resumes
the run from its checkpoint — and used to finish it silently, leaving the
tracker at ``started`` and the webhook unsent, so the client polled forever.

A run declares its originator in its inputs under :data:`COMPLETION_NOTIFY_KEY`
(``{"kind": ..., ...}``; captured into ``Checkpoint.inputs`` with the rest of
the request context). After a sweep-resumed run returns, the sweep calls the
notifier registered for that kind. Kinds are resolved through a registry, never
an import path read from the checkpoint, so a stored row cannot name code to
run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from baselith.core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "COMPLETION_NOTIFY_KEY",
    "CompletionNotifier",
    "notify_resumed_completion",
    "register_completion_notifier",
]

#: Run-input key carrying ``{"kind": <registered kind>, ...}``.
COMPLETION_NOTIFY_KEY = "completion_notify"

#: ``notifier(spec, result)``: ``spec`` is the stored dict, ``result`` what
#: ``process`` returned.
CompletionNotifier = Callable[[dict[str, Any], dict[str, Any]], Awaitable[None]]

_notifiers: dict[str, CompletionNotifier] = {}


def register_completion_notifier(kind: str, notifier: CompletionNotifier) -> None:
    """Register (or replace) the notifier for ``kind``."""
    _notifiers[kind] = notifier


def _builtin(kind: str) -> CompletionNotifier | None:
    """Notifiers shipped with core, imported on first use."""
    if kind == "task_queue.agent_run":
        from baselith.core.task_queue.jobs.agent_run import notify_resumed_run

        return notify_resumed_run
    return None


async def notify_resumed_completion(checkpoint: Any, result: Any) -> bool:
    """Run the originator's notifier for a sweep-resumed run, if it declared one.

    Failures are logged, never raised: the run itself is finished either way.

    Returns:
        True when a notifier ran.
    """
    inputs = getattr(checkpoint, "inputs", None) or {}
    spec = inputs.get(COMPLETION_NOTIFY_KEY)
    if not isinstance(spec, dict) or not isinstance(result, dict):
        return False
    kind = str(spec.get("kind", ""))
    try:
        notifier = _notifiers.get(kind) or _builtin(kind)
    except ImportError as exc:
        logger.warning(
            "run_completion_notifier_unavailable kind=%s error=%s", kind, exc
        )
        return False
    if notifier is None:
        logger.warning("run_completion_notifier_unknown kind=%s", kind)
        return False
    try:
        await notifier(spec, result)
    except Exception as exc:
        logger.warning("run_completion_notify_failed kind=%s error=%s", kind, exc)
        return False
    return True
