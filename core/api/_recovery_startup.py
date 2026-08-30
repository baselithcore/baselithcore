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


async def start_checkpoint_recovery(
    background_tasks: set[asyncio.Task[Any]],
) -> None:
    """Init the shared checkpoint store and schedule the recovery sweep.

    No-op unless ``ORCHESTRATOR_CHECKPOINT_ENABLED``; the sweep additionally
    requires ``checkpoint_resume_on_startup``. The sweep task is registered in
    ``background_tasks`` so the event loop cannot garbage-collect it.
    """
    try:
        from core.orchestration.checkpoint_factory import (
            initialize_default_checkpoint_store,
        )

        checkpoint_store = await initialize_default_checkpoint_store()
        if checkpoint_store is None:
            return
        logger.info("🧬 Checkpoint store ready (durable runs + /approvals)")

        from core.config.orchestration import get_orchestration_config

        if not get_orchestration_config().checkpoint_resume_on_startup:
            return

        async def _recover() -> None:
            from core.chat import chat_service
            from core.orchestration.recovery import resume_interrupted_runs

            try:
                report = await resume_interrupted_runs(
                    chat_service.agent,
                    checkpoint_store,
                    lock=_build_recovery_lock(),
                )
                logger.info(
                    "🧬 Crash recovery: %d resumed, %d failed, %d skipped",
                    len(report.resumed),
                    len(report.failed),
                    len(report.skipped),
                )
            except Exception as recovery_exc:
                logger.warning("Crash recovery sweep failed: %s", recovery_exc)

        recovery_task = asyncio.create_task(_recover())
        background_tasks.add(recovery_task)
        recovery_task.add_done_callback(background_tasks.discard)
    except Exception as exc:
        logger.warning("Checkpoint store initialization failed: %s", exc)


__all__ = ["start_checkpoint_recovery"]
