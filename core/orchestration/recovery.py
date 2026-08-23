"""
Crash recovery for checkpointed runs.

``CheckpointStore.list_resumable`` surfaces runs that survived a process
crash (`running`) or are paused for review (`awaiting_approval`). This module
is the consumer that closes the always-on loop: at startup (or on demand) it
re-enters interrupted `running` runs via ``process(run_id=..., resume=True)``
— completed tool steps replay from the store, so recovery is idempotent.

Runs paused ``awaiting_approval`` are intentionally left alone: they are
waiting for a human decision (the /approvals API), not for a restart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.observability.logging import get_logger
from core.orchestration.checkpoint import STATUS_RUNNING, CheckpointStore

logger = get_logger(__name__)


@dataclass
class RecoveryReport:
    """Outcome of one recovery sweep."""

    resumed: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


async def resume_interrupted_runs(
    orchestrator: Any,
    store: CheckpointStore,
    *,
    tenant_id: str | None = None,
    max_runs: int = 20,
) -> RecoveryReport:
    """Resume runs left in the ``running`` state by a crash/restart.

    Args:
        orchestrator: Object exposing ``process(query, run_id=..., resume=True)``
            and wired with the same ``store``.
        store: The shared checkpoint store.
        tenant_id: Optional tenant scope.
        max_runs: Upper bound per sweep — a backlog of interrupted runs is
            drained across sweeps rather than hammering providers at boot.

    Returns:
        RecoveryReport listing resumed, failed (run_id → error) and skipped
        (non-``running``, e.g. awaiting approval) runs.

    Note:
        Two bounds compose here. ``list_resumable`` returns at most one page
        (``DEFAULT_RESUMABLE_LIMIT``), so a crash that left tens of thousands of
        runs behind never materializes the whole backlog at startup; this sweep
        then re-enters at most ``max_runs`` of that page. The remainder is
        picked up by later sweeps — resumed runs leave the resumable set as they
        complete, so the page advances. The call is deliberately made without an
        explicit ``limit`` so third-party stores predating the parameter keep
        working.
    """
    report = RecoveryReport()
    run_ids = await store.list_resumable(tenant_id)
    for run_id in run_ids[:max_runs]:
        checkpoint = await store.load(run_id)
        if checkpoint is None or checkpoint.status != STATUS_RUNNING:
            report.skipped.append(run_id)
            continue
        try:
            await orchestrator.process(
                checkpoint.query or "", run_id=run_id, resume=True
            )
            report.resumed.append(run_id)
            logger.info("recovery_resumed run=%s", run_id)
        except Exception as exc:
            # One poisoned run must not block the rest of the sweep.
            report.failed[run_id] = str(exc)
            logger.warning("recovery_failed run=%s error=%s", run_id, exc)
    if len(run_ids) > max_runs:
        # ``run_ids`` is one bounded page, so this is a lower bound on the real
        # backlog, not a total.
        logger.info(
            "recovery_backlog remaining_at_least=%d (max_runs=%d per sweep)",
            len(run_ids) - max_runs,
            max_runs,
        )
    return report


__all__ = ["RecoveryReport", "resume_interrupted_runs"]
