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

import time
from dataclasses import dataclass, field
from typing import Any

from core.observability.logging import get_logger
from core.orchestration.checkpoint import (
    STATUS_FAILED,
    STATUS_RUNNING,
    CheckpointStore,
)

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
    lock: Any | None = None,
) -> RecoveryReport:
    """Resume runs left in the ``running`` state by a crash/restart.

    Args:
        orchestrator: Object exposing ``process(query, run_id=..., resume=True)``
            and wired with the same ``store``.
        store: The shared checkpoint store.
        tenant_id: Optional tenant scope.
        max_runs: Upper bound per sweep — a backlog of interrupted runs is
            drained across sweeps rather than hammering providers at boot.
        lock: Optional cross-replica mutex (``DistributedLock``-compatible:
            ``acquire(blocking=False)``/``release()``). With ``WEB_CONCURRENCY
            > 1`` or multiple replicas every worker runs this sweep at boot;
            the lock makes exactly one of them do the work — a worker that
            loses the non-blocking race skips the sweep entirely. Acquisition
            errors fail OPEN (the sweep still runs): recovery matters more
            than exclusion, and the worst case is today's duplicate sweep.

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
    held = False
    if lock is not None:
        try:
            held = bool(await lock.acquire(blocking=False))
        except Exception as exc:
            logger.warning(
                "recovery_lock_unavailable error=%s (sweeping without "
                "cross-replica exclusion)",
                exc,
            )
            held = False
            lock = None
        else:
            if not held:
                logger.info("recovery_sweep_skipped: another replica holds the lock")
                return report
    try:
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
            # ``run_ids`` is one bounded page, so this is a lower bound on the
            # real backlog, not a total.
            logger.info(
                "recovery_backlog remaining_at_least=%d (max_runs=%d per sweep)",
                len(run_ids) - max_runs,
                max_runs,
            )
    finally:
        if lock is not None and held:
            await lock.release()
    return report


@dataclass
class StaleSweepReport:
    """Outcome of one stale-run sweep."""

    stale: list[str] = field(default_factory=list)
    checked: int = 0


async def sweep_stale_runs(
    store: CheckpointStore,
    *,
    max_age_seconds: float,
    tenant_id: str | None = None,
    max_runs: int = 50,
    now: float | None = None,
) -> StaleSweepReport:
    """Fail ``running`` runs that stopped making progress.

    A liveness probe answers HTTP while an agent loop is wedged; the
    checkpoint knows better. A run's last progress is the newer of its
    ``updated_at`` (bumped on every step save) and the per-attempt loop
    heartbeat ``plugin_data["loop_last_progress_at"]``. A ``running`` run
    whose last progress is older than ``max_age_seconds`` is marked
    ``failed`` with an explanatory error, so operators see a wedge instead
    of an eternally "running" ghost. Runs ``awaiting_approval`` are never
    swept — they are waiting on a human, not stuck.

    Args:
        store: The shared checkpoint store.
        max_age_seconds: Progress-silence threshold.
        tenant_id: Optional tenant scope.
        max_runs: Bound per sweep.
        now: Clock override (tests).

    Returns:
        StaleSweepReport with the failed run ids and how many were checked.
    """
    report = StaleSweepReport()
    current = now if now is not None else time.time()
    run_ids = await store.list_resumable(tenant_id)
    for run_id in run_ids[:max_runs]:
        checkpoint = await store.load(run_id)
        if checkpoint is None or checkpoint.status != STATUS_RUNNING:
            continue
        report.checked += 1
        heartbeat = 0.0
        raw = checkpoint.plugin_data.get("loop_last_progress_at")
        if isinstance(raw, int | float):
            heartbeat = float(raw)
        last_progress = max(checkpoint.updated_at, heartbeat)
        silence = current - last_progress
        if silence <= max_age_seconds:
            continue
        checkpoint.status = STATUS_FAILED
        checkpoint.error = (
            f"stale: no progress for {silence:.0f}s (threshold {max_age_seconds:.0f}s)"
        )
        await store.save(checkpoint)
        report.stale.append(run_id)
        logger.warning("stale_run_failed run=%s silence=%.0fs", run_id, silence)
    return report


__all__ = [
    "RecoveryReport",
    "StaleSweepReport",
    "resume_interrupted_runs",
    "sweep_stale_runs",
]
