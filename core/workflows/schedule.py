"""In-process cron scheduling for workflow definitions.

``WorkflowScheduler`` keeps a registry of workflows carrying a cron
``schedule`` and executes the due ones through a ``WorkflowExecutor``.
Deterministic by design: time is injected via a clock override, ``due`` /
``run_due`` take an explicit ``now``, and the class spawns no background
tasks itself — callers may run :meth:`WorkflowScheduler.run_forever` as a
task of their own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from core.observability.logging import get_logger
from core.task_queue.cron import CronExpression

from .builder import WorkflowDefinition
from .executor import ExecutionStatus, WorkflowResult

logger = get_logger(__name__)

__all__ = ["SupportsWorkflowExecute", "WebhookEmitter", "WorkflowScheduler"]


class SupportsWorkflowExecute(Protocol):
    """Structural type for workflow executors (``WorkflowExecutor`` or mock)."""

    async def execute(
        self,
        workflow: WorkflowDefinition,
        initial_input: Any = None,
        checkpoint: Any = None,
    ) -> WorkflowResult:
        """Execute a workflow and return its result."""
        ...


class WebhookEmitter(Protocol):
    """Structural type for webhook services (``WebhookService`` or mock)."""

    async def emit(
        self, event_type: str, data: dict[str, Any], *, tenant_id: str = "default"
    ) -> list[Any]:
        """Emit an event to all subscribed endpoints."""
        ...


ExecutorFactory = Callable[[], SupportsWorkflowExecute]
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


@dataclass
class _Entry:
    """A registered workflow with its parsed cron and next fire time."""

    workflow: WorkflowDefinition
    cron: CronExpression
    next_run_at: datetime


class WorkflowScheduler:
    """Registry and in-process runner for cron-scheduled workflows.

    Example:
        scheduler = WorkflowScheduler(executor, webhook_service=webhooks)
        scheduler.register(workflow)          # workflow.schedule required
        await scheduler.run_due(now)          # deterministic single pass
        task = asyncio.create_task(scheduler.run_forever(interval=30.0))
    """

    def __init__(
        self,
        executor: SupportsWorkflowExecute | ExecutorFactory,
        *,
        clock: Clock | None = None,
        webhook_service: WebhookEmitter | None = None,
    ) -> None:
        """Initialize the scheduler.

        Args:
            executor: A workflow executor, or a zero-argument factory
                returning one (resolved per run, useful for fresh executors).
            clock: Override returning the current UTC time; defaults to
                ``datetime.now(UTC)``. Used when ``now`` is not supplied and
                when computing the first fire time at registration.
            webhook_service: Optional webhook service used to emit a
                workflow's ``on_failure`` event when a scheduled run fails.
        """
        self._executor = executor
        self._clock: Clock = clock or _utc_now
        self._webhooks = webhook_service
        self._entries: dict[str, _Entry] = {}

    def _resolve_executor(self) -> SupportsWorkflowExecute:
        """Return the executor, calling the factory when one was given."""
        if hasattr(self._executor, "execute"):
            return self._executor
        return self._executor()

    def register(self, workflow: WorkflowDefinition) -> datetime:
        """Register a workflow for scheduled execution.

        Args:
            workflow: A definition with a cron ``schedule`` set.

        Returns:
            The computed first fire time (UTC).

        Raises:
            ValueError: If the workflow has no ``schedule`` or it is not a
                valid cron expression.
        """
        if not workflow.schedule:
            raise ValueError(
                f"Workflow {workflow.id!r} has no schedule; set "
                "WorkflowDefinition.schedule to a cron expression first"
            )
        cron = CronExpression.parse(workflow.schedule)
        next_run_at = cron.next_after(self._clock())
        self._entries[workflow.id] = _Entry(
            workflow=workflow, cron=cron, next_run_at=next_run_at
        )
        logger.info(
            "workflow_scheduled",
            workflow_id=workflow.id,
            schedule=workflow.schedule,
            next_run_at=next_run_at.isoformat(),
        )
        return next_run_at

    def unregister(self, workflow_id: str) -> bool:
        """Remove a workflow from the schedule; returns whether it existed."""
        return self._entries.pop(workflow_id, None) is not None

    def next_run_at(self, workflow_id: str) -> datetime | None:
        """Return the next fire time for a registered workflow, if any."""
        entry = self._entries.get(workflow_id)
        return entry.next_run_at if entry else None

    def due(self, now: datetime | None = None) -> list[WorkflowDefinition]:
        """Return the workflows whose next fire time is at or before ``now``.

        Args:
            now: Reference instant (naive treated as UTC); defaults to the
                scheduler clock.
        """
        moment = _as_utc(now) if now is not None else self._clock()
        return [
            entry.workflow
            for entry in self._entries.values()
            if entry.next_run_at <= moment
        ]

    async def run_due(self, now: datetime | None = None) -> list[WorkflowResult]:
        """Execute every due workflow once and reschedule each.

        Executor exceptions are contained per workflow (converted into a
        failed :class:`WorkflowResult`); a failed run emits the workflow's
        ``on_failure`` webhook event best-effort (emit errors are logged,
        never raised).

        Args:
            now: Reference instant (naive treated as UTC); defaults to the
                scheduler clock.

        Returns:
            The results of the runs performed in this pass, in fire order.
        """
        moment = _as_utc(now) if now is not None else self._clock()
        due_entries = [
            entry for entry in self._entries.values() if entry.next_run_at <= moment
        ]
        results: list[WorkflowResult] = []
        for entry in sorted(due_entries, key=lambda e: e.next_run_at):
            entry.next_run_at = entry.cron.next_after(moment)
            result = await self._run_one(entry.workflow)
            results.append(result)
        return results

    async def _run_one(self, workflow: WorkflowDefinition) -> WorkflowResult:
        """Execute one workflow, containing errors and emitting on failure."""
        try:
            result = await self._resolve_executor().execute(workflow)
        except Exception as exc:
            logger.error(
                "scheduled_workflow_error", workflow_id=workflow.id, error=str(exc)
            )
            result = WorkflowResult(
                workflow_id=workflow.id,
                status=ExecutionStatus.FAILED,
                error=f"Scheduled run raised {type(exc).__name__}: {exc}",
            )
        if result.status == ExecutionStatus.FAILED:
            await self._emit_failure(workflow, result)
        return result

    async def _emit_failure(
        self, workflow: WorkflowDefinition, result: WorkflowResult
    ) -> None:
        """Best-effort emission of the workflow's ``on_failure`` event."""
        if self._webhooks is None or not workflow.on_failure:
            return
        try:
            await self._webhooks.emit(
                workflow.on_failure,
                {
                    "workflow_id": workflow.id,
                    "workflow_name": workflow.name,
                    "schedule": workflow.schedule,
                    "status": result.status.value,
                    "error": result.error,
                },
            )
        except Exception as exc:
            logger.warning(
                "scheduled_workflow_webhook_error",
                workflow_id=workflow.id,
                event_type=workflow.on_failure,
                error=str(exc),
            )

    async def run_forever(self, interval: float = 30.0) -> None:
        """Poll ``run_due`` on an interval until cancelled.

        A thin loop for callers to run as a task of their own
        (``asyncio.create_task(scheduler.run_forever())``); cancellation
        propagates cleanly between passes.

        Args:
            interval: Seconds to sleep between passes.
        """
        while True:
            await self.run_due(self._clock())
            await asyncio.sleep(interval)
