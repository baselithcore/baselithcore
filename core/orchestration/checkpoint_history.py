"""State history and time-travel over versioned checkpoint snapshots.

The base checkpoint flow (:mod:`core.orchestration.checkpoint`) keeps exactly
one live row per run — enough to resume after a crash, but the past is
overwritten on every save. When history is enabled
(``ORCHESTRATOR_CHECKPOINT_HISTORY_ENABLED``), stores additionally append an
immutable snapshot of the checkpoint at every version, and this module turns
those snapshots into LangGraph-style time-travel primitives:

* :func:`get_state_history` — version-ascending summaries of a run's states.
* :func:`get_state` — the full checkpoint exactly as it was at a version.
* :func:`fork_run` — copy the state at a version into a **new** run: the fork
  starts ``running`` with the source's recorded steps up to that point, so
  resuming it replays those steps (no side effects re-executed) and continues
  live from the fork point. Rewind = fork at an earlier version.

History support is duck-typed like the ``save_step`` fast-path: stores expose
optional ``list_snapshots`` / ``load_snapshot`` methods; against a store
without them these helpers degrade to "no history" (empty list / ``None``)
instead of failing, so protocol-only stores keep working.
"""

from __future__ import annotations

import uuid
from typing import Any

from core.observability.logging import get_logger
from core.orchestration.checkpoint import (
    STATUS_RUNNING,
    Checkpoint,
    CheckpointStore,
)

logger = get_logger(__name__)


async def get_state_history(
    store: CheckpointStore, run_id: str
) -> list[dict[str, Any]]:
    """Version-ascending snapshot summaries for a run.

    Each summary is ``{version, status, step, updated_at}``. Returns an empty
    list when the store records no history (disabled, or a store without
    snapshot support).
    """
    list_snapshots = getattr(store, "list_snapshots", None)
    if list_snapshots is None:
        return []
    return await list_snapshots(run_id)


async def get_state(
    store: CheckpointStore, run_id: str, version: int
) -> Checkpoint | None:
    """The full checkpoint state as recorded at ``version``, or ``None``."""
    load_snapshot = getattr(store, "load_snapshot", None)
    if load_snapshot is None:
        return None
    return await load_snapshot(run_id, version)


async def fork_run(
    store: CheckpointStore,
    run_id: str,
    version: int,
    new_run_id: str | None = None,
) -> Checkpoint | None:
    """Fork a run from its state at ``version`` into a fresh resumable run.

    The fork keeps the source's query/intent/budget and — crucially — the
    ``steps`` idempotency map recorded up to the snapshot, so
    ``process(run_id=<fork>, resume=True)`` replays those steps without
    re-executing side effects and diverges live after the fork point.
    Terminal fields (``answer``/``error``/``pending_approval``) are cleared
    and the version counter restarts at 0 for the new run.

    Returns:
        The persisted fork checkpoint, or ``None`` when the source run or
        version has no recorded snapshot.
    """
    snapshot = await get_state(store, run_id, version)
    if snapshot is None:
        return None
    fork = Checkpoint(
        run_id=new_run_id or uuid.uuid4().hex,
        tenant_id=snapshot.tenant_id,
        query=snapshot.query,
        intent=snapshot.intent,
        status=STATUS_RUNNING,
        step=snapshot.step,
        budget=snapshot.budget,
        trajectory=snapshot.trajectory,
        plugin_data=snapshot.plugin_data,
        steps=snapshot.steps,
    )
    await store.save(fork)
    logger.info(
        "checkpoint_fork source=%s version=%d fork=%s steps=%d",
        run_id,
        version,
        fork.run_id,
        len(fork.steps),
    )
    return fork


__all__ = ["fork_run", "get_state", "get_state_history"]
