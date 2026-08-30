"""Unit tests for the in-process workflow scheduler."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from core.workflows.builder import WorkflowDefinition
from core.workflows.executor import ExecutionStatus, WorkflowResult
from core.workflows.schedule import WorkflowScheduler


def _dt(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


class FakeExecutor:
    """Mock executor recording calls and returning canned results."""

    def __init__(
        self,
        status: ExecutionStatus = ExecutionStatus.COMPLETED,
        error: str | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.status = status
        self.error = error
        self.raises = raises
        self.calls: list[WorkflowDefinition] = []

    async def execute(
        self,
        workflow: WorkflowDefinition,
        initial_input: Any = None,
        checkpoint: Any = None,
    ) -> WorkflowResult:
        self.calls.append(workflow)
        if self.raises is not None:
            raise self.raises
        return WorkflowResult(
            workflow_id=workflow.id, status=self.status, error=self.error
        )


class FakeWebhookService:
    """Mock webhook service recording emitted events."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.raises = raises
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(
        self, event_type: str, data: dict[str, Any], *, tenant_id: str = "default"
    ) -> list[Any]:
        if self.raises is not None:
            raise self.raises
        self.events.append((event_type, data))
        return []


def _workflow(
    schedule: str | None = "0 12 * * *", on_failure: str | None = None
) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="scheduled", schedule=schedule, on_failure=on_failure
    )


class TestWorkflowDefinitionScheduleFields:
    def test_defaults_are_none(self) -> None:
        wf = WorkflowDefinition(name="plain")
        assert wf.schedule is None
        assert wf.on_failure is None

    def test_invalid_cron_schedule_rejected(self) -> None:
        with pytest.raises(ValueError):
            WorkflowDefinition(name="bad", schedule="not a cron")

    def test_valid_cron_schedule_accepted(self) -> None:
        wf = WorkflowDefinition(name="ok", schedule="*/5 * * * *")
        assert wf.schedule == "*/5 * * * *"

    def test_serialization_round_trip(self) -> None:
        wf = _workflow(schedule="30 6 * * 1", on_failure="workflow.failed")
        data = wf.to_dict()
        assert data["schedule"] == "30 6 * * 1"
        assert data["on_failure"] == "workflow.failed"
        restored = WorkflowDefinition.from_dict(data)
        assert restored.schedule == "30 6 * * 1"
        assert restored.on_failure == "workflow.failed"

    def test_from_dict_without_schedule_keys(self) -> None:
        restored = WorkflowDefinition.from_dict({"id": "w1", "name": "legacy"})
        assert restored.schedule is None
        assert restored.on_failure is None


class TestRegisterAndDue:
    def test_register_requires_schedule(self) -> None:
        scheduler = WorkflowScheduler(FakeExecutor())
        with pytest.raises(ValueError):
            scheduler.register(_workflow(schedule=None))

    def test_register_computes_next_fire_time(self) -> None:
        now = _dt(2026, 1, 5, 10, 0)
        scheduler = WorkflowScheduler(FakeExecutor(), clock=lambda: now)
        wf = _workflow(schedule="0 12 * * *")
        scheduler.register(wf)
        assert scheduler.due(now) == []
        assert scheduler.due(_dt(2026, 1, 5, 11, 59)) == []
        assert scheduler.due(_dt(2026, 1, 5, 12, 0)) == [wf]

    def test_unregister(self) -> None:
        now = _dt(2026, 1, 5, 10, 0)
        scheduler = WorkflowScheduler(FakeExecutor(), clock=lambda: now)
        wf = _workflow()
        scheduler.register(wf)
        assert scheduler.unregister(wf.id) is True
        assert scheduler.unregister(wf.id) is False
        assert scheduler.due(_dt(2026, 1, 5, 12, 0)) == []


class TestRunDue:
    async def test_run_due_executes_and_reschedules(self) -> None:
        executor = FakeExecutor()
        now = _dt(2026, 1, 5, 10, 0)
        scheduler = WorkflowScheduler(executor, clock=lambda: now)
        wf = _workflow(schedule="0 12 * * *")
        scheduler.register(wf)

        fire = _dt(2026, 1, 5, 12, 0)
        results = await scheduler.run_due(fire)
        assert len(results) == 1
        assert executor.calls == [wf]
        # Rescheduled strictly after the fire time: not due again today.
        assert scheduler.due(fire) == []
        assert scheduler.due(_dt(2026, 1, 6, 12, 0)) == [wf]

    async def test_run_due_skips_not_due(self) -> None:
        executor = FakeExecutor()
        now = _dt(2026, 1, 5, 10, 0)
        scheduler = WorkflowScheduler(executor, clock=lambda: now)
        scheduler.register(_workflow(schedule="0 12 * * *"))
        results = await scheduler.run_due(_dt(2026, 1, 5, 11, 0))
        assert results == []
        assert executor.calls == []

    async def test_executor_factory_supported(self) -> None:
        executor = FakeExecutor()
        now = _dt(2026, 1, 5, 10, 0)
        scheduler = WorkflowScheduler(lambda: executor, clock=lambda: now)
        wf = _workflow(schedule="0 12 * * *")
        scheduler.register(wf)
        await scheduler.run_due(_dt(2026, 1, 5, 12, 0))
        assert executor.calls == [wf]

    async def test_failed_run_emits_on_failure_webhook(self) -> None:
        executor = FakeExecutor(status=ExecutionStatus.FAILED, error="boom")
        webhooks = FakeWebhookService()
        now = _dt(2026, 1, 5, 10, 0)
        scheduler = WorkflowScheduler(
            executor, clock=lambda: now, webhook_service=webhooks
        )
        wf = _workflow(schedule="0 12 * * *", on_failure="workflow.failed")
        scheduler.register(wf)

        await scheduler.run_due(_dt(2026, 1, 5, 12, 0))
        assert len(webhooks.events) == 1
        event_type, payload = webhooks.events[0]
        assert event_type == "workflow.failed"
        assert payload["workflow_id"] == wf.id
        assert payload["error"] == "boom"

    async def test_executor_exception_is_contained_and_emits_webhook(self) -> None:
        executor = FakeExecutor(raises=RuntimeError("exploded"))
        webhooks = FakeWebhookService()
        now = _dt(2026, 1, 5, 10, 0)
        scheduler = WorkflowScheduler(
            executor, clock=lambda: now, webhook_service=webhooks
        )
        wf = _workflow(schedule="0 12 * * *", on_failure="workflow.failed")
        scheduler.register(wf)

        results = await scheduler.run_due(_dt(2026, 1, 5, 12, 0))
        assert len(results) == 1
        assert results[0].status == ExecutionStatus.FAILED
        assert "exploded" in (results[0].error or "")
        assert len(webhooks.events) == 1
        assert webhooks.events[0][1]["error"] == results[0].error
        # Still rescheduled after a failure.
        assert scheduler.due(_dt(2026, 1, 6, 12, 0)) == [wf]

    async def test_webhook_failure_never_raises(self) -> None:
        executor = FakeExecutor(status=ExecutionStatus.FAILED, error="boom")
        webhooks = FakeWebhookService(raises=RuntimeError("webhook down"))
        now = _dt(2026, 1, 5, 10, 0)
        scheduler = WorkflowScheduler(
            executor, clock=lambda: now, webhook_service=webhooks
        )
        scheduler.register(_workflow(schedule="0 12 * * *", on_failure="wf.failed"))
        results = await scheduler.run_due(_dt(2026, 1, 5, 12, 0))
        assert len(results) == 1  # no exception escaped

    async def test_no_webhook_when_no_on_failure_or_service(self) -> None:
        executor = FakeExecutor(status=ExecutionStatus.FAILED, error="boom")
        now = _dt(2026, 1, 5, 10, 0)
        # No webhook service at all.
        scheduler = WorkflowScheduler(executor, clock=lambda: now)
        scheduler.register(_workflow(schedule="0 12 * * *", on_failure="wf.failed"))
        await scheduler.run_due(_dt(2026, 1, 5, 12, 0))

        # Service present but workflow has no on_failure event.
        webhooks = FakeWebhookService()
        scheduler2 = WorkflowScheduler(
            executor, clock=lambda: now, webhook_service=webhooks
        )
        scheduler2.register(_workflow(schedule="0 12 * * *", on_failure=None))
        await scheduler2.run_due(_dt(2026, 1, 5, 12, 0))
        assert webhooks.events == []


class TestRunForever:
    async def test_run_forever_is_cancellation_safe(self) -> None:
        executor = FakeExecutor()
        now = _dt(2026, 1, 5, 10, 0)
        scheduler = WorkflowScheduler(executor, clock=lambda: now)
        task = asyncio.create_task(scheduler.run_forever(interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_run_forever_runs_due_workflows(self) -> None:
        executor = FakeExecutor()
        current = {"now": _dt(2026, 1, 5, 10, 0)}
        scheduler = WorkflowScheduler(executor, clock=lambda: current["now"])
        wf = _workflow(schedule="0 12 * * *")
        scheduler.register(wf)

        task = asyncio.create_task(scheduler.run_forever(interval=0.01))
        await asyncio.sleep(0.03)
        current["now"] = _dt(2026, 1, 5, 12, 0)
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert executor.calls == [wf]
