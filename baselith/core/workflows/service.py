"""Process-wide workflow service: catalog, runtime, trigger auth, scheduler.

Started by the app lifespan when ``WORKFLOWS_ENABLED`` is set. It wires the
pieces that have to agree with each other:

* loads the catalog directory;
* keeps the cron scheduler in step with catalog changes (a workflow saved
  through the API with a ``schedule`` starts firing without a restart);
* keeps the MCP tool list in step with catalog changes;
* gives the HTTP router and the CLI one place to find all of it.

Every step is fail-open at startup: a bad definition file or an unreachable
collaborator is logged and skipped, never a reason for the app not to boot.
"""

from __future__ import annotations

import asyncio
from typing import Any

from baselith.core.config.workflows import WorkflowsConfig, get_workflows_config
from baselith.core.observability.logging import get_logger

from .builder import WorkflowDefinition
from .catalog import WorkflowCatalog
from .executor import WorkflowResult
from .runtime import WorkflowRuntime
from .schedule import WorkflowScheduler
from .triggers import TriggerVerifier

logger = get_logger(__name__)

__all__ = [
    "WorkflowService",
    "get_workflow_service",
    "set_workflow_service",
    "start_workflow_service",
    "stop_workflow_service",
]


class _SchedulerExecutor:
    """Adapts the runtime to the scheduler's ``execute`` contract."""

    def __init__(self, runtime: WorkflowRuntime) -> None:
        self._runtime = runtime

    async def execute(
        self,
        workflow: WorkflowDefinition,
        initial_input: Any = None,
        checkpoint: Any = None,
    ) -> WorkflowResult:
        _, result = await self._runtime.run_definition(workflow, source="schedule")
        assert result is not None  # run_definition always yields a result
        return result


class WorkflowService:
    """Owns the catalog and runtime and the background tasks around them."""

    def __init__(self, config: WorkflowsConfig | None = None) -> None:
        self.config = config or get_workflows_config()
        self.catalog = WorkflowCatalog(self.config.directory)
        self.runtime = WorkflowRuntime(self.catalog, config=self.config)
        self.verifier = TriggerVerifier(
            self.config.trigger_secret, self.config.trigger_tolerance_seconds
        )
        self.scheduler: WorkflowScheduler | None = None
        self._scheduler_task: asyncio.Task[None] | None = None
        self._mcp_server: Any | None = None

    async def start(self, app: Any | None = None) -> None:
        """Load the catalog and start the scheduler and MCP sync."""
        self.catalog.subscribe(self._on_catalog_event)
        report = self.catalog.load_directory()
        logger.info(
            "workflow_catalog_loaded loaded=%d failed=%d directory=%s",
            len(report.loaded),
            len(report.failed),
            self.catalog.directory,
        )
        self._mcp_server = getattr(getattr(app, "state", None), "mcp_server", None)
        if self._mcp_server is not None:
            for workflow in self.catalog.list():
                self._sync_mcp(workflow.id)
        if self.config.scheduler_enabled:
            self._start_scheduler()
        if not self.verifier.configured:
            logger.info(
                "workflow_trigger_disabled: WORKFLOWS_TRIGGER_SECRET is not set, "
                "POST /workflows/{id}/trigger will answer 503"
            )

    def _start_scheduler(self) -> None:
        webhooks = None
        try:
            from baselith.core.config.webhooks import get_webhook_config

            if get_webhook_config().enabled:
                from baselith.core.webhooks.service import get_webhook_service

                webhooks = get_webhook_service()
        except Exception as exc:
            logger.warning("workflow_scheduler_webhooks_unavailable: %s", exc)
        self.scheduler = WorkflowScheduler(
            _SchedulerExecutor(self.runtime), webhook_service=webhooks
        )
        for workflow in self.catalog.list():
            self._sync_schedule(workflow.id)
        self._scheduler_task = asyncio.create_task(
            self.scheduler.run_forever(self.config.scheduler_interval_seconds),
            name="workflow-scheduler",
        )

    def _on_catalog_event(self, event: str, workflow_id: str) -> None:
        self._sync_schedule(workflow_id)
        self._sync_mcp(workflow_id)

    def _sync_schedule(self, workflow_id: str) -> None:
        if self.scheduler is None:
            return
        workflow = self.catalog.get(workflow_id)
        if workflow is not None and workflow.schedule:
            self.scheduler.register(workflow)
        else:
            self.scheduler.unregister(workflow_id)

    def _sync_mcp(self, workflow_id: str) -> None:
        server = self._mcp_server
        if server is None:
            return
        from .mcp_tools import register_workflow_tool, unregister_workflow_tool

        workflow = self.catalog.get(workflow_id)
        try:
            if workflow is None:
                unregister_workflow_tool(server, workflow_id)
            else:
                register_workflow_tool(server, workflow, self.runtime)
        except Exception as exc:
            logger.warning("workflow_mcp_sync_failed id=%s error=%s", workflow_id, exc)

    async def stop(self) -> None:
        """Stop the scheduler and cancel runs still in flight."""
        task, self._scheduler_task = self._scheduler_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.runtime.aclose()


_service: WorkflowService | None = None


def get_workflow_service() -> WorkflowService | None:
    """The running service, or ``None`` when workflows are not enabled."""
    return _service


def set_workflow_service(service: WorkflowService | None) -> None:
    """Install (or clear) the process-wide service; for the lifespan and tests."""
    global _service
    _service = service


async def start_workflow_service(app: Any) -> None:
    """Start the service when ``WORKFLOWS_ENABLED``; otherwise do nothing."""
    config = get_workflows_config()
    if not config.enabled:
        return
    service = WorkflowService(config)
    await service.start(app)
    set_workflow_service(service)
    app.state.workflow_service = service


async def stop_workflow_service(app: Any) -> None:
    """Stop the service started by :func:`start_workflow_service` (idempotent)."""
    service = getattr(app.state, "workflow_service", None)
    if service is None:
        return
    app.state.workflow_service = None
    set_workflow_service(None)
    await service.stop()
