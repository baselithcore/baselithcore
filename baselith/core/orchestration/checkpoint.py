"""Durable checkpointing and resume for the agent loop.

Before this module, a crash mid-request lost the entire run: trajectory, partial
progress, and any completed-but-not-yet-committed tool work. Tool steps were not
idempotent, so a naive retry re-ran side effects.

This adds a **checkpoint** — a JSON-serializable snapshot of run state (query,
intent, budget, trajectory, plugin data, per-step results) persisted to a
:class:`CheckpointStore` — plus a :class:`CheckpointManager` that wraps each tool
step with a deterministic-replay idempotency guard, modelled on LangGraph's
checkpointer / Temporal's event history:

* On a fresh run, each ``run_step`` executes the tool, records its result keyed
  by ``(tool, args-hash, occurrence)`` — see :mod:`baselith.core.orchestration.call_keys`
  — and persists the checkpoint.
* On resume, the manager replays the handler from the top with the loaded
  ``steps`` map: already-recorded steps return their stored result **without
  re-executing** (no duplicated side effects), and a step not recorded yet runs
  for real. The key is content-addressed, not positional, so a resumed pass
  that requests the same effects in a different order — the normal case for an
  LLM-driven loop that regenerates its turns — still replays each of them.
  Checkpoints written under the old ``(cursor, tool, args-hash)`` keys stay
  readable: a miss on the new key falls back to the positional one.

The store is pluggable: an in-memory implementation ships here for tests and
single-process use; Postgres (``checkpoint_postgres``) and SQLite
(``checkpoint_sqlite``) backends survive process restarts.

Concurrency is explicit. Every write is a compare-and-swap on ``version``: a
writer holding a stale copy gets :class:`CheckpointConflictError` and the run
stops instead of clobbering newer state. Who may execute a run is decided by a
per-run lease (``owner`` + ``lease_expires_at``, see
:mod:`baselith.core.orchestration.checkpoint_lease`), claimed atomically on
start and resume, renewed on every step and by a heartbeat, released at the end
of the attempt.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from baselith.core.observability.agent_spans import tool_span
from baselith.core.observability.logging import get_logger
from baselith.core.orchestration.call_keys import (
    CallOccurrences,
    call_step_key,
    step_key,
)
from baselith.core.orchestration.checkpoint_errors import (
    CheckpointConflictError,
    CheckpointLeaseHeldError,
)
from baselith.core.orchestration.checkpoint_lease import LeaseHolder
from baselith.core.orchestration.checkpoint_scope import ScopeHost

if TYPE_CHECKING:  # re-exported below through the module __getattr__ shim
    from baselith.core.orchestration.checkpoint_approvals import (
        ApprovalPrincipal,
        record_approval_decision,
    )
    from baselith.core.orchestration.checkpoint_init import init_checkpoint
    from baselith.core.orchestration.checkpoint_memory import InMemoryCheckpointStore

logger = get_logger(__name__)

# Checkpoint lifecycle states.
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_AWAITING_APPROVAL = "awaiting_approval"

# States a run can be resumed from (crash recovery + human-in-the-loop pause).
RESUMABLE_STATUSES = (STATUS_RUNNING, STATUS_AWAITING_APPROVAL)

#: Default bound on :meth:`CheckpointStore.list_resumable`. A crash that leaves
#: tens of thousands of runs ``running`` must not turn the next boot into a full
#: table scan materialized in one list: recovery drains the backlog across
#: sweeps (``resume_interrupted_runs`` re-enters at most ``max_runs`` per pass),
#: so one page is all a sweep can ever use. 500 is well above that per-sweep
#: budget, keeping the approvals/inspection read paths unaffected in practice.
DEFAULT_RESUMABLE_LIMIT = 500

#: Hard ceiling for an explicit ``limit`` argument, so a caller cannot reinstate
#: the unbounded query.
MAX_RESUMABLE_LIMIT = 5000


@dataclass
class Checkpoint:
    """A JSON-serializable snapshot of one agent-loop run.

    Attributes:
        run_id: Stable identifier used to resume.
        tenant_id: Owning tenant (row-scoped in the persistent store).
        query: The user query that started the run.
        intent: Classified intent (restored on resume so classification isn't
            re-run).
        status: ``running`` | ``completed`` | ``failed``.
        step: Highest replay cursor reached (progress indicator).
        budget: ``LoopBudgetSnapshot`` as a dict, refreshed on every step
            save; restored so caps continue across resume rather than
            resetting to a full budget.
        trajectory: Ordered list of executed steps (audit / trajectory eval).
        plugin_data: Handler/plugin scratch state carried across resume.
        answer: Final answer when completed.
        error: Failure reason when failed.
        steps: Idempotency map ``step_key -> {tool_name, args, result, at}``.
        pending_approval: Human-in-the-loop pause payload when status is
            ``awaiting_approval``: the tool/category awaiting review plus, once
            recorded, the reviewer's ``decision``.
        inputs: JSON-safe subset of the request context, restored on resume
            (see :mod:`baselith.core.orchestration.checkpoint_inputs`).
        owner: Executor holding the run's lease, ``None`` when unowned.
        lease_expires_at: Unix time the owner's lease runs out.
        version: Compare-and-swap counter: a save succeeds only while the
            stored version still equals this one.
        created_at / updated_at: Unix timestamps.
    """

    run_id: str
    tenant_id: str | None = None
    query: str = ""
    intent: str | None = None
    status: str = STATUS_RUNNING
    step: int = 0
    budget: dict[str, Any] = field(default_factory=dict)
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    plugin_data: dict[str, Any] = field(default_factory=dict)
    answer: Any | None = None
    error: str | None = None
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_approval: dict[str, Any] | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    owner: str | None = None
    lease_expires_at: float | None = None
    version: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (for JSONB persistence)."""
        return {
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "query": self.query,
            "intent": self.intent,
            "status": self.status,
            "step": self.step,
            "budget": self.budget,
            "trajectory": self.trajectory,
            "plugin_data": self.plugin_data,
            "answer": self.answer,
            "error": self.error,
            "steps": self.steps,
            "pending_approval": self.pending_approval,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            # Appended, not interleaved: the /runs state body keeps its order.
            "inputs": self.inputs,
            "owner": self.owner,
            "lease_expires_at": self.lease_expires_at,
        }

    def head_dict(self) -> dict[str, Any]:
        """Every field but the two that grow per step (``steps``, ``trajectory``).

        What an incremental step save rewrites besides the new step, so it
        persists the same state a full save would (budget, plugin data, …).
        """
        head = self.to_dict()
        del head["steps"], head["trajectory"]
        return head

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint:
        """Rebuild from a persisted dict (ignores unknown keys)."""
        kwargs: dict[str, Any] = {k: data[k] for k in _CHECKPOINT_FIELDS if k in data}
        return cls(**kwargs)


_CHECKPOINT_FIELDS = {
    "run_id",
    "tenant_id",
    "query",
    "intent",
    "status",
    "step",
    "budget",
    "trajectory",
    "plugin_data",
    "answer",
    "error",
    "steps",
    "pending_approval",
    "inputs",
    "owner",
    "lease_expires_at",
    "version",
    "created_at",
    "updated_at",
}


@runtime_checkable
class CheckpointStore(Protocol):
    """Persistence contract for checkpoints."""

    async def save(self, checkpoint: Checkpoint) -> None:
        """Insert, or update while the stored version equals ``checkpoint.version``.

        Bumps ``checkpoint.version`` on success. A store implementing the
        concurrency contract raises :class:`CheckpointConflictError` when the
        stored version moved on.
        """
        ...

    async def load(self, run_id: str) -> Checkpoint | None:
        """Load a checkpoint by ``run_id``, or None if absent."""
        ...

    async def delete(self, run_id: str) -> None:
        """Remove a checkpoint (e.g. after successful completion)."""
        ...

    async def list_resumable(
        self, tenant_id: str | None = None, *, limit: int | None = None
    ) -> list[str]:
        """Return ``run_id``s still in the ``running`` state (crash recovery).

        Args:
            tenant_id: Optional tenant scope.
            limit: Maximum ids to return; ``None`` uses
                :data:`DEFAULT_RESUMABLE_LIMIT`. The listing is always bounded.
        """
        ...


class CheckpointManager(LeaseHolder, ScopeHost):
    """Runtime façade a handler uses to make its tool steps durable.

    Exposed to handlers on the orchestration context as ``context["checkpoint"]``.
    Wrap each tool invocation in :meth:`run_step`; call :meth:`complete` /
    :meth:`fail` at the end of the run. Composite runners record their units
    under :meth:`scope` (see :mod:`~baselith.core.orchestration.checkpoint_scope`).

    Args:
        store: The checkpoint store.
        checkpoint: The run's state.
        budget: The request's ``LoopBudget``; when given, its snapshot is
            written with every save, so a crash never loses the counters.
        owner: This attempt's lease owner id (``None``: no lease).
        lease_seconds: Lease length the heartbeat and step saves renew.
    """

    def __init__(
        self,
        store: CheckpointStore,
        checkpoint: Checkpoint,
        *,
        budget: Any | None = None,
        owner: str | None = None,
        lease_seconds: float | None = None,
    ) -> None:
        self.store = store
        self.checkpoint = checkpoint
        self.budget = budget
        self._init_lease(owner, lease_seconds)
        # Replay cursor for the *current* pass: trajectory order, and the
        # legacy positional key. Reset to 0 each pass.
        self._cursor = 0
        # Occurrences of each (tool, args) pair this pass, which is what makes
        # step keys content-addressed. Per pass for the same reason.
        self._occurrences = CallOccurrences()

    @property
    def run_id(self) -> str:
        return self.checkpoint.run_id

    @property
    def resumed(self) -> bool:
        """True when this run already has recorded steps (i.e. a resume)."""
        return bool(self.checkpoint.steps)

    def next_occurrence(self, tool_name: str, args: Any) -> int:
        """Claim the occurrence number of the next ``(tool_name, args)`` step.

        For a caller that must derive a second key for the same call — an
        idempotency-ledger key inside the step — from the same occurrence the
        step itself uses; pass the result to :meth:`run_step` as
        ``occurrence``. A step replayed from the checkpoint never reaches the
        ledger, so two independent counters would drift apart and the next
        genuine repeat would collide with the replayed call's ledger row.

        Args:
            tool_name: Tool name.
            args: Tool arguments, exactly as passed to :meth:`run_step`.

        Returns:
            The occurrence number (``0`` for the first such call this pass).
        """
        return self._occurrences.next(tool_name, args)

    async def run_step(
        self,
        tool_name: str,
        args: Any,
        fn: Callable[[], Awaitable[Any]],
        *,
        category: str = "tool",
        occurrence: int | None = None,
    ) -> Any:
        """Execute (or replay) one idempotent tool step.

        On a fresh step, ``fn`` runs, its result is recorded and the checkpoint
        persisted. On replay (result already recorded for this key), ``fn`` is
        **not** called and the stored result is returned — so re-running after a
        crash never duplicates a side effect.

        Args:
            tool_name: Name of the tool being invoked.
            args: Tool arguments (used in the idempotency key; should be
                JSON-serializable for the persistent store).
            fn: Zero-arg coroutine that performs the actual call.
            category: Step category for the trajectory record.
            occurrence: Occurrence number from :meth:`next_occurrence`; drawn
                here when omitted.

        Returns:
            The tool result (freshly computed or replayed).
        """
        from baselith.core.orchestration.run_events import EventType, publish_run_event

        cursor = self._cursor
        self._cursor += 1
        if occurrence is None:
            occurrence = self._occurrences.next(tool_name, args)
        key = call_step_key(tool_name, args, occurrence)

        step_meta = {"tool_name": tool_name, "category": category, "cursor": cursor}
        publish_run_event(self.run_id, EventType.TOOL_CALL, step_meta)
        recorded = self.checkpoint.steps.get(key)
        if recorded is None:
            # A checkpoint written under the positional scheme; matches only
            # when this pass reached the step at its original position.
            recorded = self.checkpoint.steps.get(step_key(cursor, tool_name, args))
        if recorded is not None:
            logger.debug(
                "checkpoint_replay run=%s step=%s tool=%s",
                self.run_id,
                cursor,
                tool_name,
            )
            publish_run_event(
                self.run_id, EventType.TOOL_RESULT, {**step_meta, "replayed": True}
            )
            return recorded["result"]

        # Fence the side effect: an attempt that lost its lease must not run it.
        await self.ensure_lease()
        # The tool span attributes the call to whichever agent is running, so a
        # topology view can draw agent-to-tool edges. Replayed steps get no
        # span: nothing executed, and a zero-duration bar would misreport the
        # run's real cost.
        with tool_span(tool_name, attributes={"baselith.step.cursor": cursor}):
            result = await fn()
        publish_run_event(
            self.run_id, EventType.TOOL_RESULT, {**step_meta, "replayed": False}
        )
        entry = {
            "tool_name": tool_name,
            "args": args,
            "result": result,
            "category": category,
            "at": time.time(),
        }
        trajectory_entry = {
            "cursor": cursor,
            "tool": tool_name,
            "args": args,
            "category": category,
        }
        self.checkpoint.steps[key] = entry
        self.checkpoint.trajectory.append(trajectory_entry)
        self.checkpoint.step = max(self.checkpoint.step, cursor + 1)
        self.checkpoint.status = STATUS_RUNNING
        self._sync_budget()
        self.extend_local_lease()
        # Incremental when the store supports it; serialised either way, so
        # concurrent units of a composite runner never race the version CAS.
        await self._persist((key, entry, trajectory_entry))
        return result

    def update_budget(self, snapshot: Any) -> None:
        """Record a budget snapshot on the checkpoint; the next save persists it."""
        if hasattr(snapshot, "__dict__"):
            self.checkpoint.budget = dict(snapshot.__dict__)
        elif isinstance(snapshot, dict):
            self.checkpoint.budget = dict(snapshot)

    def _sync_budget(self) -> None:
        """Copy the live budget counters onto the checkpoint before a save."""
        if self.budget is not None:
            self.update_budget(self.budget.snapshot())

    async def complete(self, answer: Any | None = None) -> None:
        """Mark the run completed and persist the final state."""
        self.checkpoint.status = STATUS_COMPLETED
        self.checkpoint.answer = answer
        self._sync_budget()
        await self._persist()

    async def fail(self, error: str) -> None:
        """Mark the run failed and persist, so it can be inspected or resumed."""
        self.checkpoint.status = STATUS_FAILED
        self.checkpoint.error = error
        self._sync_budget()
        await self._persist()

    async def await_approval(self, tool_name: str, category: str) -> None:
        """Pause the run pending human approval and persist that state.

        Records which tool/category is waiting so an operator (or an approval
        UI) can review it, then resume the run once a decision is recorded via
        :func:`record_approval_decision`.
        """
        self.checkpoint.status = STATUS_AWAITING_APPROVAL
        self.checkpoint.pending_approval = {
            "tool_name": tool_name,
            "category": category,
            "requested_at": time.time(),
        }
        self._sync_budget()
        await self._persist()

    def approval_decision(self, tool_name: str, category: str) -> bool | None:
        """Recorded reviewer decision for this tool/category, if any.

        Returns True (approved) / False (denied) when a decision matching the
        pending request exists, None when nothing was recorded (fresh gate).
        """
        pending = self.checkpoint.pending_approval
        if not pending or "decision" not in pending:
            return None
        if pending.get("tool_name") != tool_name or pending.get("category") != category:
            return None
        return bool(pending["decision"].get("approved"))


# The operator-side approval flow lives in ``checkpoint_approvals`` and
# ``init_checkpoint`` in ``checkpoint_init`` (module size cap); both are
# re-exported here so ``from baselith.core.orchestration.checkpoint import
# record_approval_decision`` keeps working. Deferred, like the in-memory store
# below: the siblings import the contract from this module.
def __getattr__(name: str) -> Any:  # pragma: no cover - import shim
    if name == "init_checkpoint":
        from baselith.core.orchestration.checkpoint_init import init_checkpoint

        return init_checkpoint
    if name == "InMemoryCheckpointStore":
        from baselith.core.orchestration.checkpoint_memory import (
            InMemoryCheckpointStore,
        )

        return InMemoryCheckpointStore
    if name in ("ApprovalPrincipal", "record_approval_decision"):
        from baselith.core.orchestration import checkpoint_approvals

        return getattr(checkpoint_approvals, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DEFAULT_RESUMABLE_LIMIT",
    "MAX_RESUMABLE_LIMIT",
    "RESUMABLE_STATUSES",
    "STATUS_AWAITING_APPROVAL",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_RUNNING",
    "ApprovalPrincipal",
    "Checkpoint",
    "CheckpointConflictError",
    "CheckpointLeaseHeldError",
    "CheckpointManager",
    "CheckpointStore",
    "InMemoryCheckpointStore",
    "init_checkpoint",
    "record_approval_decision",
    "step_key",
]
