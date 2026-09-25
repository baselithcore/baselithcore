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
  by ``(tool, args-hash, occurrence)`` — see :mod:`core.orchestration.call_keys`
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
single-process use; a Postgres-backed one lives in ``checkpoint_postgres`` for
durability across process restarts.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from core.observability.agent_spans import tool_span
from core.observability.logging import get_logger
from core.orchestration.call_keys import CallOccurrences, call_step_key

if TYPE_CHECKING:  # re-exported below through the module __getattr__ shim
    from core.orchestration.checkpoint_approvals import (
        ApprovalPrincipal,
        record_approval_decision,
    )
    from core.orchestration.checkpoint_memory import InMemoryCheckpointStore

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


def _canonical_args_hash(args: Any) -> str:
    """Stable short hash of tool args for the idempotency key.

    Uses canonical JSON (sorted keys) so equal args always hash identically;
    falls back to ``repr`` for values JSON can't encode.
    """
    try:
        encoded = json.dumps(args, sort_keys=True, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        encoded = repr(args)
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def step_key(cursor: int, tool_name: str, args: Any) -> str:
    """Legacy positional key for a single tool step.

    Superseded by :func:`core.orchestration.call_keys.call_step_key`: the
    cursor made the key depend on the path the run took, so a resumed pass
    that reached a recorded step at a different position re-executed it. Still
    consulted by :meth:`CheckpointManager.run_step` as a fallback, so a
    checkpoint written before the switch resumes as it always did.
    """
    return f"{cursor}:{tool_name}:{_canonical_args_hash(args)}"


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
        budget: ``LoopBudgetSnapshot`` as a dict; restored so caps continue
            across resume rather than resetting to a full budget.
        trajectory: Ordered list of executed steps (audit / trajectory eval).
        plugin_data: Handler/plugin scratch state carried across resume.
        answer: Final answer when completed.
        error: Failure reason when failed.
        steps: Idempotency map ``step_key -> {tool_name, args, result, at}``.
        pending_approval: Human-in-the-loop pause payload when status is
            ``awaiting_approval``: the tool/category awaiting review plus, once
            recorded, the reviewer's ``decision``.
        version: Monotonic counter for optimistic concurrency in the store.
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
        }

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
    "version",
    "created_at",
    "updated_at",
}


@runtime_checkable
class CheckpointStore(Protocol):
    """Persistence contract for checkpoints."""

    async def save(self, checkpoint: Checkpoint) -> None:
        """Insert or update the checkpoint (upsert by ``run_id``)."""
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


class CheckpointManager:
    """Runtime façade a handler uses to make its tool steps durable.

    Exposed to handlers on the orchestration context as ``context["checkpoint"]``.
    Wrap each tool invocation in :meth:`run_step`; call :meth:`complete` /
    :meth:`fail` at the end of the run.
    """

    def __init__(self, store: CheckpointStore, checkpoint: Checkpoint) -> None:
        self.store = store
        self.checkpoint = checkpoint
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
        from core.orchestration.run_events import EventType, publish_run_event

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
        # Stores may expose an incremental fast-path that writes only the new
        # step instead of re-serializing the whole accumulated state (which
        # made an n-step run O(n²) bytes over the wire). Optional by design:
        # any store implementing just the CheckpointStore protocol still works.
        save_step = getattr(self.store, "save_step", None)
        if save_step is not None:
            await save_step(self.checkpoint, key, entry, trajectory_entry)
        else:
            await self.store.save(self.checkpoint)
        return result

    def update_budget(self, snapshot: Any) -> None:
        """Record the latest budget snapshot on the checkpoint (not persisted)."""
        if hasattr(snapshot, "__dict__"):
            self.checkpoint.budget = dict(snapshot.__dict__)
        elif isinstance(snapshot, dict):
            self.checkpoint.budget = dict(snapshot)

    async def complete(self, answer: Any | None = None) -> None:
        """Mark the run completed and persist the final state."""
        self.checkpoint.status = STATUS_COMPLETED
        self.checkpoint.answer = answer
        await self.store.save(self.checkpoint)

    async def fail(self, error: str) -> None:
        """Mark the run failed and persist, so it can be inspected or resumed."""
        self.checkpoint.status = STATUS_FAILED
        self.checkpoint.error = error
        await self.store.save(self.checkpoint)

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
        await self.store.save(self.checkpoint)

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


async def init_checkpoint(
    store: CheckpointStore,
    query: str,
    context: dict[str, Any],
    intent: str | None,
    budget: Any,
    run_id: str | None,
    resume: bool,
) -> CheckpointManager:
    """Create a fresh checkpoint or resume an existing one.

    On resume, restores the budget counters from the stored snapshot so caps
    continue across the restart rather than resetting to a full budget.
    (Extracted from the execution mixin for the module size cap.)
    """
    import uuid

    tenant_id = context.get("tenant_id")
    if resume and run_id:
        existing = await store.load(run_id)
        if existing is not None:
            b = existing.budget or {}
            budget.iterations = int(b.get("iterations", 0))
            budget.tool_calls = int(b.get("tool_calls", 0))
            budget.cost_usd = float(b.get("cost_usd", 0.0))
            # Tokens are the counter a long run is most likely to be near, and
            # dropping them handed the resumed run a full token cap — the one
            # way a crash-loop could spend without bound. ``context_tokens``
            # rides along so the allocation signal (and the token-pressure
            # fallback that reads it) survives the restart too.
            budget.tokens = int(b.get("tokens", 0))
            budget.context_tokens = int(b.get("context_tokens", 0))
            existing.status = STATUS_RUNNING
            logger.info(
                "checkpoint_resume run=%s steps=%d",
                run_id,
                len(existing.steps),
            )
            return CheckpointManager(store, existing)
        logger.warning(
            "checkpoint_resume_miss run=%s not found; starting fresh", run_id
        )

    checkpoint = Checkpoint(
        run_id=run_id or uuid.uuid4().hex,
        tenant_id=tenant_id,
        query=query,
        intent=intent,
    )
    await store.save(checkpoint)
    return CheckpointManager(store, checkpoint)


# The operator-side approval flow lives in ``checkpoint_approvals`` (module
# size cap) and is re-exported here so ``from core.orchestration.checkpoint
# import record_approval_decision`` keeps working. Deferred, like the in-memory
# store below: both siblings import the contract from this module.
def __getattr__(name: str) -> Any:  # pragma: no cover - import shim
    if name == "InMemoryCheckpointStore":
        from core.orchestration.checkpoint_memory import InMemoryCheckpointStore

        return InMemoryCheckpointStore
    if name in ("ApprovalPrincipal", "record_approval_decision"):
        from core.orchestration import checkpoint_approvals

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
    "CheckpointManager",
    "CheckpointStore",
    "InMemoryCheckpointStore",
    "init_checkpoint",
    "record_approval_decision",
    "step_key",
]
