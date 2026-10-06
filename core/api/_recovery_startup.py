"""Checkpoint-store startup + crash-recovery sweep (extracted from lifespan).

With ``WEB_CONCURRENCY > 1`` (or multiple replicas) every worker runs the
lifespan, so an unguarded sweep re-entered the same interrupted runs once per
worker — duplicate agent executions and duplicate LLM spend. The sweep is
therefore wrapped in the Redis-backed
:class:`~core.resilience.distributed_lock.DistributedLock` when the cache
backend is Redis; a worker that loses the non-blocking race skips the sweep
entirely. Without Redis (single-node/local cache mode) the sweep runs
unguarded, which is safe for a single process.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.config import get_storage_config
from core.observability.logging import get_logger

logger = get_logger(__name__)

_RECOVERY_LOCK_NAME = "checkpoint_recovery_sweep"
# Resumed runs re-enter full agent loops, so the sweep can hold the lock for
# minutes: auto_renew extends this TTL while the sweep is alive, and a crashed
# holder frees the lock within one TTL.
_RECOVERY_LOCK_TTL_MS = 60_000


def _build_recovery_lock() -> Any | None:
    """Build the cross-replica sweep lock, or ``None`` when Redis is not in play."""
    storage_config = get_storage_config()
    if getattr(storage_config, "cache_backend", "") != "redis" or not getattr(
        storage_config, "cache_redis_url", ""
    ):
        return None
    try:
        from core.resilience.distributed_lock import get_distributed_lock

        return get_distributed_lock(
            _RECOVERY_LOCK_NAME, ttl_ms=_RECOVERY_LOCK_TTL_MS, auto_renew=True
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Recovery lock unavailable (%s); sweeping without cross-replica exclusion",
            exc,
        )
        return None


#: Backoff for re-initializing the store after a boot with PostgreSQL down.
_RETRY_INITIAL_DELAY_S = 2.0
_RETRY_MAX_DELAY_S = 30.0


def _register(
    task: asyncio.Task[Any], background_tasks: set[asyncio.Task[Any]]
) -> None:
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


async def stale_sweep_loop(
    checkpoint_store: Any,
    *,
    interval_seconds: float,
    stale_after_seconds: float,
    max_cycles: int | None = None,
) -> None:
    """Fail silent ``running`` checkpoints on an interval; never resume them.

    The resume-off counterpart of
    :func:`~core.orchestration.recovery.recovery_sweep_loop`. Without it a run
    interrupted by a crash or restart stayed ``running`` forever when auto
    resume is off: no worker owns it, nothing will touch it again, and it
    reads as in-flight to every operator and dashboard. The progress
    threshold is the same one the full recovery cycle uses, so a run still
    executing on another worker (which keeps bumping its heartbeat) is left
    alone. No cross-replica lock: two replicas failing the same run write the
    same terminal state.

    Args:
        checkpoint_store: The shared checkpoint store.
        interval_seconds: Delay between sweeps. Must be > 0.
        stale_after_seconds: Progress-silence threshold.
        max_cycles: Stop after this many sweeps (tests); ``None`` runs until
            cancelled.

    Raises:
        ValueError: ``interval_seconds`` is not positive.
    """
    from core.orchestration.recovery import sweep_stale_runs

    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    logger.info(
        "🧬 Stale-run sweep every %.0fs (fail after %.0fs silent; auto resume off)",
        interval_seconds,
        stale_after_seconds,
    )
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        cycle += 1
        try:
            report = await sweep_stale_runs(
                checkpoint_store, max_age_seconds=stale_after_seconds
            )
            if report.stale:
                logger.warning(
                    "stale_sweep failed=%d orphaned run(s) (auto resume off)",
                    len(report.stale),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("stale_sweep_failed error=%s", exc)
        if max_cycles is not None and cycle >= max_cycles:
            return
        await asyncio.sleep(interval_seconds)


def _schedule_recovery_sweep(
    checkpoint_store: Any, background_tasks: set[asyncio.Task[Any]]
) -> None:
    """Start the recovery sweep loop when ``checkpoint_resume_on_startup``.

    With resume off, a fail-only loop runs instead (unless
    ``recovery_stale_sweep_enabled`` is off): nothing is re-entered, but a run
    a crash left ``running`` is marked failed once it has been silent for
    ``recovery_stale_after_seconds`` rather than staying orphaned forever.
    """
    from core.config.orchestration import get_orchestration_config

    config = get_orchestration_config()
    if not config.checkpoint_resume_on_startup:
        if config.recovery_stale_sweep_enabled:
            _register(
                asyncio.create_task(
                    stale_sweep_loop(
                        checkpoint_store,
                        interval_seconds=config.recovery_sweep_interval_seconds,
                        stale_after_seconds=config.recovery_stale_after_seconds,
                    )
                ),
                background_tasks,
            )
        return

    async def _recover() -> None:
        from core.chat import chat_service
        from core.orchestration.recovery import recovery_sweep_loop

        logger.info(
            "🧬 Crash recovery sweeps every %.0fs "
            "(resume after %.0fs idle, fail after %.0fs)",
            config.recovery_sweep_interval_seconds,
            config.recovery_resume_after_seconds,
            config.recovery_stale_after_seconds,
        )
        await recovery_sweep_loop(
            chat_service.agent,
            checkpoint_store,
            interval_seconds=config.recovery_sweep_interval_seconds,
            stale_after_seconds=config.recovery_stale_after_seconds,
            resume_after_seconds=config.recovery_resume_after_seconds,
            lock_factory=_build_recovery_lock,
        )

    _register(asyncio.create_task(_recover()), background_tasks)


async def _initialize_when_database_returns(
    background_tasks: set[asyncio.Task[Any]],
) -> None:
    """Re-probe PostgreSQL with backoff; initialize the store once it answers.

    Runs as a background task so boot never waits on a database that is down.
    Cancelled with the other startup tasks at shutdown.
    """
    from core.db.reachability import probe_postgres
    from core.orchestration.checkpoint_factory import (
        CheckpointStoreUnavailableError,
        initialize_default_checkpoint_store,
    )

    delay = _RETRY_INITIAL_DELAY_S
    while True:
        await asyncio.sleep(delay)
        delay = min(delay * 2, _RETRY_MAX_DELAY_S)
        if not await probe_postgres():
            continue
        try:
            checkpoint_store = await initialize_default_checkpoint_store()
        except CheckpointStoreUnavailableError:
            continue
        except Exception as exc:
            logger.warning("Checkpoint store initialization retry failed: %s", exc)
            continue
        logger.info("🧬 Checkpoint store ready after PostgreSQL recovered")
        if checkpoint_store is not None:
            _schedule_recovery_sweep(checkpoint_store, background_tasks)
        return


async def start_checkpoint_recovery(
    background_tasks: set[asyncio.Task[Any]],
) -> None:
    """Init the shared checkpoint store and schedule the recovery sweeps.

    No-op unless ``ORCHESTRATOR_CHECKPOINT_ENABLED``. Resuming interrupted
    runs additionally requires ``checkpoint_resume_on_startup``; without it
    only the stale-run sweep runs (:func:`stale_sweep_loop`). The sweep task is registered in
    ``background_tasks`` so the event loop cannot garbage-collect it, and it
    is cancelled with the rest of them at shutdown.

    The task is a *loop*, not a one-shot: it re-enters interrupted runs and
    fails wedged ones every ``recovery_sweep_interval_seconds``. A boot-only
    sweep left every post-startup wedge to the next restart — a ``running``
    checkpoint that stopped making progress stayed invisible to liveness
    probes, which only ever see that the process still answers HTTP.

    With a Postgres-backed store and the boot reachability probe already
    failed, initialization does not wait out ``DB_POOL_TIMEOUT``: boot goes
    on degraded and a background task initializes the store (and starts the
    sweep) once PostgreSQL answers again.
    """
    try:
        from core.orchestration.checkpoint_factory import (
            CheckpointStoreUnavailableError,
            initialize_default_checkpoint_store,
        )

        try:
            checkpoint_store = await initialize_default_checkpoint_store()
        except CheckpointStoreUnavailableError:
            logger.warning(
                "🧬 Checkpoint store deferred: PostgreSQL unreachable at boot; "
                "retrying in the background"
            )
            _register(
                asyncio.create_task(
                    _initialize_when_database_returns(background_tasks)
                ),
                background_tasks,
            )
            return
        if checkpoint_store is None:
            return
        logger.info("🧬 Checkpoint store ready (durable runs + /approvals)")
        _schedule_recovery_sweep(checkpoint_store, background_tasks)
    except Exception as exc:
        logger.warning("Checkpoint store initialization failed: %s", exc)


__all__ = ["stale_sweep_loop", "start_checkpoint_recovery"]
