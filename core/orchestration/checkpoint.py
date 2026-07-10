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
  by ``(cursor, tool, args-hash)``, and persists the checkpoint.
* On resume, the manager replays the handler from the top with the loaded
  ``steps`` map: already-recorded steps return their stored result **without
  re-executing** (no duplicated side effects), and the first not-yet-recorded
  step runs for real.

The store is pluggable: an in-memory implementation ships here for tests and
single-process use; a Postgres-backed one lives in ``checkpoint_postgres`` for
durability across process restarts.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from core.observability.logging import get_logger

logger = get_logger(__name__)

# Checkpoint lifecycle states.
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


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
    """Deterministic idempotency key for a single tool step.

    Includes the replay cursor position, the tool name, and an args hash so a
    divergent replay (different tool or args at the same position) gets a fresh
    key and executes rather than reusing a stale result.
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

    async def list_resumable(self, tenant_id: str | None = None) -> list[str]:
        """Return ``run_id``s still in the ``running`` state (crash recovery)."""
        ...


class InMemoryCheckpointStore:
    """In-process checkpoint store for tests and single-process use.

    Deep-copies on save and load so callers can't mutate stored state through a
    retained reference — matching the isolation a real datastore provides.
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def save(self, checkpoint: Checkpoint) -> None:
        checkpoint.updated_at = time.time()
        checkpoint.version += 1
        self._store[checkpoint.run_id] = copy.deepcopy(checkpoint.to_dict())

    async def load(self, run_id: str) -> Checkpoint | None:
        data = self._store.get(run_id)
        return Checkpoint.from_dict(copy.deepcopy(data)) if data is not None else None

    async def delete(self, run_id: str) -> None:
        self._store.pop(run_id, None)

    async def list_resumable(self, tenant_id: str | None = None) -> list[str]:
        return [
            rid
            for rid, d in self._store.items()
            if d.get("status") == STATUS_RUNNING
            and (tenant_id is None or d.get("tenant_id") == tenant_id)
        ]


class CheckpointManager:
    """Runtime façade a handler uses to make its tool steps durable.

    Exposed to handlers on the orchestration context as ``context["checkpoint"]``.
    Wrap each tool invocation in :meth:`run_step`; call :meth:`complete` /
    :meth:`fail` at the end of the run.
    """

    def __init__(self, store: CheckpointStore, checkpoint: Checkpoint) -> None:
        self.store = store
        self.checkpoint = checkpoint
        # Replay cursor for the *current* pass. Reset to 0 each pass so a resumed
        # run realigns keys with the persisted ``steps`` map.
        self._cursor = 0

    @property
    def run_id(self) -> str:
        return self.checkpoint.run_id

    @property
    def resumed(self) -> bool:
        """True when this run already has recorded steps (i.e. a resume)."""
        return bool(self.checkpoint.steps)

    async def run_step(
        self,
        tool_name: str,
        args: Any,
        fn: Callable[[], Awaitable[Any]],
        *,
        category: str = "tool",
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

        Returns:
            The tool result (freshly computed or replayed).
        """
        cursor = self._cursor
        key = step_key(cursor, tool_name, args)
        self._cursor += 1

        recorded = self.checkpoint.steps.get(key)
        if recorded is not None:
            logger.debug(
                "checkpoint_replay run=%s step=%s tool=%s",
                self.run_id,
                cursor,
                tool_name,
            )
            return recorded["result"]

        result = await fn()
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


__all__ = [
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_RUNNING",
    "Checkpoint",
    "CheckpointManager",
    "CheckpointStore",
    "InMemoryCheckpointStore",
    "step_key",
]
