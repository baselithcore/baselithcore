"""Create or resume the checkpoint for one orchestrated attempt.

Split from :mod:`baselith.core.orchestration.checkpoint` (module size cap) and
re-exported there, so ``from baselith.core.orchestration.checkpoint import
init_checkpoint`` keeps working.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, cast

from baselith.core.observability.logging import get_logger
from baselith.core.orchestration.checkpoint import (
    STATUS_RUNNING,
    Checkpoint,
    CheckpointManager,
    CheckpointStore,
)
from baselith.core.orchestration.checkpoint_inputs import (
    capture_run_inputs,
    restore_run_inputs,
)
from baselith.core.orchestration.checkpoint_lease import (
    configured_lease_seconds,
    new_owner_id,
    supports_leases,
)

logger = get_logger(__name__)


def _restore_budget(budget: Any, snapshot: dict[str, Any]) -> None:
    """Continue the stored counters instead of handing out a full budget.

    Tokens are the counter a long run is most likely to be near, and dropping
    them handed the resumed run a full token cap — the one way a crash loop
    could spend without bound. ``context_tokens`` rides along so the
    allocation signal (and the token-pressure fallback that reads it) survives
    the restart too. The wall-clock deadline is per attempt and restarts.
    """
    budget.iterations = int(snapshot.get("iterations", 0))
    budget.tool_calls = int(snapshot.get("tool_calls", 0))
    budget.cost_usd = float(snapshot.get("cost_usd", 0.0))
    budget.tokens = int(snapshot.get("tokens", 0))
    budget.context_tokens = int(snapshot.get("context_tokens", 0))


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

    A fresh run records the resumable part of ``context`` (see
    :mod:`~baselith.core.orchestration.checkpoint_inputs`) and, on a store with
    lease support, is created already owned by this attempt.

    A resume **claims** the run first — atomically, before anything executes —
    then restores the budget counters and the captured inputs (only the keys
    the resuming caller left unset).

    Raises:
        CheckpointLeaseHeldError: ``resume`` and another live executor owns
            the run; nothing was changed.
        CheckpointConflictError: A fresh run's ``run_id`` already exists in
            the store (it would otherwise be overwritten).
    """
    leased = supports_leases(store)
    owner = new_owner_id() if leased else None
    lease_seconds = configured_lease_seconds()
    if resume and run_id:
        existing: Checkpoint | None
        if leased:
            # Lease support is optional, so not on the CheckpointStore protocol.
            existing = await cast(Any, store).claim(run_id, owner, lease_seconds)
        else:
            existing = await store.load(run_id)
        if existing is not None:
            _restore_budget(budget, existing.budget or {})
            restored = restore_run_inputs(context, existing.inputs)
            existing.status = STATUS_RUNNING
            logger.info(
                "checkpoint_resume run=%s steps=%d restored_inputs=%s",
                run_id,
                len(existing.steps),
                restored,
            )
            return CheckpointManager(
                store,
                existing,
                budget=budget,
                owner=owner,
                lease_seconds=lease_seconds,
            )
        logger.warning(
            "checkpoint_resume_miss run=%s not found; starting fresh", run_id
        )

    checkpoint = Checkpoint(
        run_id=run_id or uuid.uuid4().hex,
        tenant_id=context.get("tenant_id"),
        query=query,
        intent=intent,
        inputs=capture_run_inputs(context),
        owner=owner,
        lease_expires_at=time.time() + lease_seconds if owner else None,
    )
    await store.save(checkpoint)
    return CheckpointManager(
        store, checkpoint, budget=budget, owner=owner, lease_seconds=lease_seconds
    )


__all__ = ["init_checkpoint"]
