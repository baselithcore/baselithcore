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

import asyncio
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


def last_progress_at(checkpoint: Any) -> float:
    """When this run last demonstrably moved.

    The newer of ``updated_at`` (bumped on every step save) and the
    per-attempt loop heartbeat ``plugin_data["loop_last_progress_at"]``. Both
    sweeps read the same value: "has this run gone quiet?" must not have two
    answers, or a run could be simultaneously fresh enough to re-enter and
    silent enough to fail.
    """
    heartbeat = 0.0
    plugin_data = getattr(checkpoint, "plugin_data", None) or {}
    raw = plugin_data.get("loop_last_progress_at")
    if isinstance(raw, int | float):
        heartbeat = float(raw)
    return max(float(getattr(checkpoint, "updated_at", 0.0) or 0.0), heartbeat)


async def _process_as_tenant(orchestrator: Any, checkpoint: Any, run_id: str) -> Any:
    """Re-enter ``process(..., resume=True)`` bound to the run's tenant.

    The ambient tenant contextvar is set for the duration of the call (so
    tenant-scoped storage, memory and plugin seams see the right owner) and
    restored afterwards, success or failure — a sweep resumes many runs and
    must not leak one run's tenant into the next.
    """
    from core.context import reset_tenant_context, set_tenant_context

    tenant_id = getattr(checkpoint, "tenant_id", None)
    context: dict[str, Any] = {"tenant_id": tenant_id} if tenant_id else {}
    token = set_tenant_context(tenant_id) if tenant_id else None
    try:
        return await orchestrator.process(
            checkpoint.query or "",
            context=context,
            run_id=run_id,
            resume=True,
        )
    finally:
        if token is not None:
            reset_tenant_context(token)


async def resume_interrupted_runs(
    orchestrator: Any,
    store: CheckpointStore,
    *,
    tenant_id: str | None = None,
    max_runs: int = 20,
    lock: Any | None = None,
    min_idle_seconds: float | None = None,
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
        min_idle_seconds: Skip runs whose last progress is newer than this —
            a run that is still moving is still owned by someone, and
            re-entering it would duplicate its side effects. ``None`` (the
            default) keeps the historical unguarded behaviour for a one-shot
            boot sweep, where nothing is executing yet;
            :func:`run_recovery_cycle` always passes a threshold because it
            runs while the process is serving traffic.

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
    from core.db.connection import system_tenant_scope

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
        # Discovery and inspection are cross-tenant by construction — the sweep
        # is looking for *whose* runs were interrupted, so it cannot borrow the
        # identity of a run it has not found yet. It is also started at boot and
        # loops forever (``core.api._recovery_startup``), so under
        # ``DB_RLS_ENABLED`` the unbound checkout raised ``TenantContextError``
        # straight into the cycle's own fail-open handler: a
        # ``recovery_cycle_failed`` warning every interval and crash recovery
        # that silently never ran. Only the reads are scoped; the resume below
        # still rebinds to the run's own tenant.
        with system_tenant_scope():
            run_ids = await store.list_resumable(tenant_id)
        for run_id in run_ids[:max_runs]:
            with system_tenant_scope():
                checkpoint = await store.load(run_id)
            if checkpoint is None or checkpoint.status != STATUS_RUNNING:
                report.skipped.append(run_id)
                continue
            # Recent progress means a worker still owns this run. The sweep is
            # periodic now, so without this guard it re-entered runs that were
            # mid-execution and ran the same agent loop a second time —
            # duplicated tool side effects and duplicated LLM spend, which is
            # exactly what crash recovery exists to avoid.
            if min_idle_seconds is not None:
                silence = time.time() - last_progress_at(checkpoint)
                if silence < min_idle_seconds:
                    report.skipped.append(run_id)
                    logger.debug(
                        "recovery_skip_active run=%s silence=%.0fs < %.0fs",
                        run_id,
                        silence,
                        min_idle_seconds,
                    )
                    continue
            try:
                # Re-enter under the run's *own* tenant. Without this the
                # resumed run inherited whatever ambient tenant the boot sweep
                # carried, so the orchestrator's tenant-isolation guard either
                # stamped the wrong tenant onto the context or rejected the
                # run outright. Both the ambient contextvar (read by storage /
                # memory / plugin seams) and the explicit context entry are
                # set, because the guard compares the two.
                await _process_as_tenant(orchestrator, checkpoint, run_id)
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
    from core.db.connection import system_tenant_scope

    report = StaleSweepReport()
    current = now if now is not None else time.time()
    # Every store call here is maintenance across all tenants: find interrupted
    # runs, read them, mark the wedged ones failed. There is no run to inherit a
    # tenant from, and the write keeps the row's own ``tenant_id`` (the store
    # scopes by column, not by the session GUC), so the sweep declares itself
    # the way the tenant purge does.
    with system_tenant_scope():
        run_ids = await store.list_resumable(tenant_id)
        for run_id in run_ids[:max_runs]:
            checkpoint = await store.load(run_id)
            if checkpoint is None or checkpoint.status != STATUS_RUNNING:
                continue
            report.checked += 1
            silence = current - last_progress_at(checkpoint)
            if silence <= max_age_seconds:
                continue
            checkpoint.status = STATUS_FAILED
            checkpoint.error = (
                f"stale: no progress for {silence:.0f}s "
                f"(threshold {max_age_seconds:.0f}s)"
            )
            await store.save(checkpoint)
            report.stale.append(run_id)
            logger.warning("stale_run_failed run=%s silence=%.0fs", run_id, silence)
    return report


def _configured_resume_after_seconds() -> float:
    """``recovery_resume_after_seconds``, or the sweep interval as fallback."""
    try:
        from core.config.orchestration import get_orchestration_config

        return float(get_orchestration_config().recovery_resume_after_seconds)
    except Exception:  # pragma: no cover - config must not break recovery
        logger.debug("recovery_resume_after_unavailable", exc_info=True)
        return 300.0


async def run_recovery_cycle(
    orchestrator: Any,
    store: CheckpointStore,
    *,
    stale_after_seconds: float,
    resume_after_seconds: float | None = None,
    tenant_id: str | None = None,
    lock: Any | None = None,
) -> tuple[RecoveryReport, StaleSweepReport]:
    """Run both halves of recovery once: resume, then fail what is wedged.

    The two sweeps are complementary and belong in the same cycle. Resuming
    first gives a merely-interrupted run its chance to finish; the stale sweep
    then closes out whatever still has not moved, so a wedged run becomes a
    visible ``failed`` instead of an eternally ``running`` ghost that every
    liveness probe reports as healthy.

    Neither sweep can abort the other: a store hiccup during the resume pass
    must not cost the deployment its stale detection, and vice versa.

    Args:
        orchestrator: Object exposing ``process(query, context=..., run_id=...,
            resume=True)``.
        store: The shared checkpoint store.
        stale_after_seconds: Progress-silence threshold for the stale sweep.
        resume_after_seconds: Progress-silence threshold before a run is
            re-entered. ``None`` reads ``recovery_resume_after_seconds`` from
            the orchestration settings. A cycle runs while the process is
            serving traffic, so this guard is what keeps it from re-entering a
            run that is still executing.
        tenant_id: Optional tenant scope.
        lock: Optional cross-replica mutex for the resume pass.

    Returns:
        The two reports, in ``(resume, stale)`` order. A sweep that raised
        yields an empty report rather than propagating.
    """
    resumed = RecoveryReport()
    stale = StaleSweepReport()
    if resume_after_seconds is None:
        resume_after_seconds = _configured_resume_after_seconds()
    try:
        resumed = await resume_interrupted_runs(
            orchestrator,
            store,
            tenant_id=tenant_id,
            lock=lock,
            min_idle_seconds=resume_after_seconds,
        )
    except Exception as exc:
        logger.warning("recovery_resume_sweep_failed error=%s", exc)
    try:
        stale = await sweep_stale_runs(
            store, max_age_seconds=stale_after_seconds, tenant_id=tenant_id
        )
    except Exception as exc:
        logger.warning("recovery_stale_sweep_failed error=%s", exc)
    return resumed, stale


async def recovery_sweep_loop(
    orchestrator: Any,
    store: CheckpointStore,
    *,
    interval_seconds: float,
    stale_after_seconds: float,
    resume_after_seconds: float | None = None,
    tenant_id: str | None = None,
    lock_factory: Any | None = None,
    max_cycles: int | None = None,
) -> None:
    """Run :func:`run_recovery_cycle` on a fixed interval until cancelled.

    Recovery that only fires at boot leaves every post-startup wedge to the
    next restart. This is the always-on half: a background task the app
    lifespan owns and cancels on shutdown.

    A failing cycle is logged and the loop continues — the whole point is to
    survive the conditions that make sweeps fail.

    Args:
        orchestrator: Object exposing ``process(..., resume=True)``.
        store: The shared checkpoint store.
        interval_seconds: Delay between cycles. Must be > 0.
        stale_after_seconds: Progress-silence threshold for the stale sweep.
        resume_after_seconds: Progress-silence threshold before a run is
            re-entered; ``None`` reads the configured default.
        tenant_id: Optional tenant scope.
        lock_factory: Optional zero-arg callable returning a fresh
            cross-replica lock for each cycle (``None`` disables exclusion).
        max_cycles: Stop after this many cycles instead of running forever
            (tests). ``None`` runs until cancelled.

    Raises:
        ValueError: ``interval_seconds`` is not positive — an unbounded busy
            loop is never the intended configuration.
    """
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        cycle += 1
        lock = None
        if lock_factory is not None:
            try:
                lock = lock_factory()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("recovery_lock_factory_failed error=%s", exc)
        try:
            resumed, stale = await run_recovery_cycle(
                orchestrator,
                store,
                stale_after_seconds=stale_after_seconds,
                resume_after_seconds=resume_after_seconds,
                tenant_id=tenant_id,
                lock=lock,
            )
            if resumed.resumed or resumed.failed or stale.stale:
                logger.info(
                    "recovery_cycle resumed=%d failed=%d stale=%d",
                    len(resumed.resumed),
                    len(resumed.failed),
                    len(stale.stale),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("recovery_cycle_failed error=%s", exc)
        if max_cycles is not None and cycle >= max_cycles:
            return
        await asyncio.sleep(interval_seconds)


__all__ = [
    "RecoveryReport",
    "StaleSweepReport",
    "last_progress_at",
    "recovery_sweep_loop",
    "resume_interrupted_runs",
    "run_recovery_cycle",
    "sweep_stale_runs",
]
