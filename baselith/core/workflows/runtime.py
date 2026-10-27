"""Run catalog workflows: limits, history, durable approval pauses.

:class:`WorkflowRuntime` is the single place a catalog workflow is executed
from outside a Python call: the HTTP router, the signed trigger, the cron
scheduler and the MCP tools all go through it, so they share one set of limits
and one run history.

* **Limits.** At most ``max_concurrent_runs`` runs are in flight; the next one
  is refused (:class:`RuntimeBusyError`) rather than queued without bound. Each
  run has a wall-clock timeout.
* **History.** The newest ``run_history_size`` runs are kept in memory for
  ``GET /workflows/runs/{id}``. It is process-local by design; the checkpoint
  store is the durable record for runs that pause.
* **Approval pauses.** A workflow with a HUMAN node runs under a durable
  checkpoint. At the gate the run ends as ``awaiting_approval`` and shows up in
  the ``/approvals`` listing; once a reviewer decides, :meth:`resume` replays
  the finished nodes from the checkpoint and carries on.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from baselith.core.config.workflows import WorkflowsConfig, get_workflows_config
from baselith.core.observability.logging import get_logger

from .builder import NodeType, WorkflowDefinition
from .catalog import WorkflowCatalog
from .executor import ExecutionStatus, WorkflowExecutor, WorkflowResult

logger = get_logger(__name__)

__all__ = [
    "INTENT_PREFIX",
    "RunRecord",
    "RuntimeBusyError",
    "UnknownRunError",
    "UnknownWorkflowError",
    "WorkflowRuntime",
    "contains_human_gate",
]

#: Checkpoint ``intent`` marking a run as a catalog workflow run.
INTENT_PREFIX = "workflow:"

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
TIMED_OUT = "timed_out"
AWAITING_APPROVAL = "awaiting_approval"

ExecutorFactory = Callable[[], WorkflowExecutor]


class UnknownWorkflowError(LookupError):
    """No workflow is registered under the requested id."""


class UnknownRunError(LookupError):
    """No resumable run exists under the requested id."""


class RuntimeBusyError(RuntimeError):
    """``max_concurrent_runs`` runs are already in flight."""


@dataclass
class RunRecord:
    """The state of one run, as reported to callers."""

    run_id: str
    workflow_id: str
    source: str
    status: str
    started_at: float
    finished_at: float | None = None
    output: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready view; ``output`` is whatever the workflow returned."""
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "source": self.source,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "output": self.output,
            "error": self.error,
        }


def contains_human_gate(workflow: WorkflowDefinition, _depth: int = 0) -> bool:
    """Whether ``workflow`` (or a nested one) has a HUMAN node.

    Only such workflows run under a checkpoint: a durable run needs every node
    output to be JSON-serializable, a cost the rest do not have to pay.
    """
    for node in workflow.nodes:
        if node.type is NodeType.HUMAN:
            return True
        nested = node.config.get("workflow")
        if _depth < 5 and node.type in (NodeType.SUBGRAPH, NodeType.ITERATION):
            sub = (
                WorkflowDefinition.from_dict(nested)
                if isinstance(nested, dict)
                else nested
            )
            if isinstance(sub, WorkflowDefinition) and contains_human_gate(
                sub, _depth + 1
            ):
                return True
    return False


def _http_credentials(config: WorkflowsConfig) -> dict[str, dict[str, str]]:
    if config.http_credentials is None:
        return {}
    try:
        data = json.loads(config.http_credentials.get_secret_value())
    except ValueError:
        logger.error("workflows_http_credentials_invalid: not valid JSON")
        return {}
    if not isinstance(data, dict) or not all(
        isinstance(v, dict) and all(isinstance(h, str) for h in v.values())
        for v in data.values()
    ):
        logger.error(
            "workflows_http_credentials_invalid: expected {name: {header: value}}"
        )
        return {}
    return data


def default_executor_factory(config: WorkflowsConfig | None = None) -> ExecutorFactory:
    """Executor factory for catalog runs.

    Plugin tools are exposed to TOOL nodes only when they declare
    ``category: read_only``. Anything that can change state needs an explicit
    registry from the embedder (``WorkflowRuntime(executor_factory=...)``): a
    definition uploaded through the API must not gain the reach of every
    installed plugin.
    """
    cfg = config or get_workflows_config()

    def _factory() -> WorkflowExecutor:
        return WorkflowExecutor(
            tools=_read_only_plugin_tools(),
            services={"http_credentials": _http_credentials(cfg)},
        )

    return _factory


def _read_only_plugin_tools() -> dict[str, Callable[..., Any]]:
    try:
        from baselith.core.di import ServiceRegistry
        from baselith.core.plugins import PluginRegistry

        if not ServiceRegistry.has(PluginRegistry):
            return {}
        plugins = ServiceRegistry.get(PluginRegistry).get_all()
    except Exception as exc:
        logger.debug("workflow_plugin_tools_unavailable: %s", exc)
        return {}
    tools: dict[str, Callable[..., Any]] = {}
    for plugin in plugins:
        if not plugin.is_initialized():
            continue
        try:
            definitions = plugin.get_mcp_tools()
        except Exception as exc:
            logger.warning("workflow_plugin_tools_failed: %s", exc)
            continue
        for tool in definitions:
            if (
                tool.get("category") == "read_only"
                and tool.get("handler")
                and tool.get("name")
            ):
                tools[tool["name"]] = tool["handler"]
    return tools


class WorkflowRuntime:
    """Executes catalog workflows under shared limits."""

    def __init__(
        self,
        catalog: WorkflowCatalog,
        *,
        config: WorkflowsConfig | None = None,
        executor_factory: ExecutorFactory | None = None,
        checkpoint_store: Any | None = None,
    ) -> None:
        """
        Args:
            catalog: Where workflows are looked up.
            config: Limits; the global workflows config when omitted.
            executor_factory: Builds the executor per run (agent/tool
                registries, services); see :func:`default_executor_factory`.
            checkpoint_store: Store for approval pauses; the process-wide
                default store (``None`` when checkpointing is off) otherwise.
        """
        self._catalog = catalog
        self._config = config or get_workflows_config()
        self._factory = executor_factory or default_executor_factory(self._config)
        self._store_override = checkpoint_store
        self._runs: OrderedDict[str, RunRecord] = OrderedDict()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._inflight = 0

    @property
    def catalog(self) -> WorkflowCatalog:
        """The catalog this runtime executes from."""
        return self._catalog

    def get_run(self, run_id: str) -> RunRecord | None:
        """A run from the in-memory history."""
        return self._runs.get(run_id)

    def list_runs(
        self, workflow_id: str | None = None, limit: int = 50
    ) -> list[RunRecord]:
        """Newest runs first, optionally for one workflow."""
        runs = reversed(self._runs.values())
        return [r for r in runs if workflow_id in (None, r.workflow_id)][:limit]

    def _store(self) -> Any | None:
        if self._store_override is not None:
            return self._store_override
        from baselith.core.orchestration.checkpoint_factory import (
            get_default_checkpoint_store,
        )

        return get_default_checkpoint_store()

    def _remember(self, record: RunRecord) -> None:
        self._runs[record.run_id] = record
        self._runs.move_to_end(record.run_id)
        while len(self._runs) > self._config.run_history_size:
            oldest = next(iter(self._runs))
            if self._runs[oldest].status == RUNNING:
                break
            self._runs.pop(oldest)

    def _require(self, workflow_id: str) -> WorkflowDefinition:
        workflow = self._catalog.get(workflow_id)
        if workflow is None:
            raise UnknownWorkflowError(workflow_id)
        return workflow

    def _reserve_slot(self) -> None:
        if self._inflight >= self._config.max_concurrent_runs:
            raise RuntimeBusyError(
                f"{self._config.max_concurrent_runs} workflow runs already in flight"
            )
        self._inflight += 1

    async def run(
        self, workflow_id: str, input_data: Any = None, *, source: str = "api"
    ) -> RunRecord:
        """Run a workflow and wait for it to finish or pause.

        Raises:
            UnknownWorkflowError: Not in the catalog.
            RuntimeBusyError: The concurrency limit is reached.
        """
        workflow = self._require(workflow_id)
        self._reserve_slot()
        record, _ = await self._execute(workflow, input_data, source)
        return record

    def submit(
        self, workflow_id: str, input_data: Any = None, *, source: str = "trigger"
    ) -> RunRecord:
        """Start a run in the background and return its ``running`` record.

        Raises:
            UnknownWorkflowError: Not in the catalog.
            RuntimeBusyError: The concurrency limit is reached.
        """
        workflow = self._require(workflow_id)
        self._reserve_slot()
        record = self._new_record(workflow, source, uuid.uuid4().hex)
        task = asyncio.ensure_future(
            self._execute(workflow, input_data, source, record=record)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return record

    async def run_definition(
        self, workflow: WorkflowDefinition, *, source: str = "schedule"
    ) -> tuple[RunRecord, WorkflowResult | None]:
        """Run an already-resolved definition (the scheduler's entry point)."""
        self._reserve_slot()
        return await self._execute(workflow, None, source)

    async def resume(self, run_id: str) -> RunRecord:
        """Continue a run that paused at a HUMAN gate after a decision.

        Raises:
            UnknownRunError: No paused workflow run has this id.
            UnknownWorkflowError: Its workflow left the catalog.
            RuntimeBusyError: The concurrency limit is reached.
        """
        store = self._store()
        checkpoint = await store.load(run_id) if store is not None else None
        if checkpoint is None or not (checkpoint.intent or "").startswith(
            INTENT_PREFIX
        ):
            raise UnknownRunError(run_id)
        workflow = self._require(checkpoint.intent[len(INTENT_PREFIX) :])
        try:
            input_data = json.loads(checkpoint.query) if checkpoint.query else None
        except ValueError:
            input_data = checkpoint.query
        self._reserve_slot()
        record, _ = await self._execute(
            workflow, input_data, "resume", run_id=run_id, resume=True
        )
        return record

    def _new_record(
        self, workflow: WorkflowDefinition, source: str, run_id: str
    ) -> RunRecord:
        record = RunRecord(
            run_id=run_id,
            workflow_id=workflow.id,
            source=source,
            status=RUNNING,
            started_at=time.time(),
        )
        self._remember(record)
        return record

    async def _execute(
        self,
        workflow: WorkflowDefinition,
        input_data: Any,
        source: str,
        *,
        record: RunRecord | None = None,
        run_id: str | None = None,
        resume: bool = False,
    ) -> tuple[RunRecord, WorkflowResult | None]:
        """Run once. The caller has already reserved an in-flight slot."""
        from baselith.core.orchestration.autonomy import ApprovalPendingError
        from baselith.core.orchestration.checkpoint import CheckpointLeaseHeldError

        record = record or self._new_record(
            workflow, source, run_id or uuid.uuid4().hex
        )
        record.status = RUNNING
        manager = None
        result: WorkflowResult | None = None
        try:
            manager = await self._open_checkpoint(workflow, input_data, record, resume)
            result = await asyncio.wait_for(
                self._factory().execute(workflow, input_data, checkpoint=manager),
                timeout=self._config.run_timeout_seconds,
            )
            record.output = result.output
            record.error = result.error
            record.status = (
                COMPLETED if result.status is ExecutionStatus.COMPLETED else FAILED
            )
            if manager is not None:
                if record.status == COMPLETED:
                    await manager.complete(result.output)
                else:
                    await manager.fail(result.error or "workflow failed")
        except ApprovalPendingError:
            record.status = AWAITING_APPROVAL
        except CheckpointLeaseHeldError:
            # Another executor owns the run: not a failure of the run itself.
            record.status = AWAITING_APPROVAL if resume else FAILED
            raise
        except TimeoutError:
            record.status, record.error = (
                TIMED_OUT,
                (f"run exceeded {self._config.run_timeout_seconds:g}s"),
            )
            await self._fail_checkpoint(manager, record.error)
        except Exception as exc:
            logger.error("workflow_run_failed id=%s error=%s", workflow.id, exc)
            record.status, record.error = FAILED, f"{type(exc).__name__}: {exc}"
            await self._fail_checkpoint(manager, record.error)
        finally:
            self._inflight -= 1
            if manager is not None:
                await manager.end_attempt()
            record.finished_at = time.time()
        if result is None:
            result = WorkflowResult(
                workflow_id=workflow.id,
                status=ExecutionStatus.FAILED,
                error=record.error or record.status,
            )
        return record, result

    async def _open_checkpoint(
        self,
        workflow: WorkflowDefinition,
        input_data: Any,
        record: RunRecord,
        resume: bool,
    ) -> Any | None:
        if not contains_human_gate(workflow):
            return None
        store = self._store()
        if store is None:
            return None  # the gate then fails closed with a clear message
        from baselith.core.orchestration.checkpoint_init import init_checkpoint
        from baselith.core.orchestration.limits import LoopBudget

        manager = await init_checkpoint(
            store,
            json.dumps(input_data, default=str),
            {},
            f"{INTENT_PREFIX}{workflow.id}",
            LoopBudget(),
            record.run_id,
            resume,
        )
        manager.start_heartbeat()
        return manager

    @staticmethod
    async def _fail_checkpoint(manager: Any | None, error: str) -> None:
        if manager is None:
            return
        try:
            await manager.fail(error)
        except Exception as exc:
            logger.warning("workflow_checkpoint_fail_write_failed error=%s", exc)

    async def aclose(self) -> None:
        """Cancel runs still in flight (shutdown)."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
