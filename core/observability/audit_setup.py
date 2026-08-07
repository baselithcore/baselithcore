"""Wiring for the durable audit trail.

Builds the global :class:`~core.observability.audit.AuditLogger` from
:class:`~core.config.audit.AuditConfig` and owns the retention sweep. Kept
separate from :mod:`core.observability.audit` (the event model) and
:mod:`core.observability.audit_chain` (the sink) so neither depends on
configuration — both stay directly constructible in tests.

Opt-in: with ``AUDIT_ENABLED`` unset :func:`configure_audit_logging` leaves the
historical logger-only behaviour untouched.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.config.audit import get_audit_config
from core.observability.audit import (
    AuditLogger,
    FileAuditSink,
    LoggerAuditSink,
    set_audit_logger,
)
from core.observability.audit_chain import SQLiteAuditSink
from core.observability.logging import get_logger

logger = get_logger(__name__)

# A daily sweep bounds how long expired records linger to ~24h past the
# horizon while keeping the DB load negligible.
_SWEEP_INTERVAL_SECONDS = 24 * 3600


def configure_audit_logging() -> AuditLogger | None:
    """Install the configured audit logger as the global instance.

    Returns the configured logger, or ``None`` when the subsystem is disabled
    (in which case the pre-existing global logger is left alone). Never raises:
    a misconfigured audit trail must not stop the application from starting —
    it is logged loudly instead.
    """
    config = get_audit_config()
    if not config.enabled:
        return None

    try:
        audit_logger = AuditLogger()
        if config.log_sink_enabled:
            audit_logger.add_sink(LoggerAuditSink())
        if config.file_path:
            from pathlib import Path

            audit_logger.add_sink(FileAuditSink(Path(config.file_path)))
        if config.db_path:
            audit_logger.add_sink(
                SQLiteAuditSink(
                    config.db_path,
                    hash_chain=config.hash_chain,
                    max_detail_chars=config.max_detail_chars,
                )
            )
        set_audit_logger(audit_logger)
        logger.info(
            "audit_trail_configured",
            extra={
                "sinks": len(audit_logger.sinks),
                "db_path": config.db_path,
                "hash_chain": config.hash_chain,
                "retention_days": config.retention_days,
            },
        )
        return audit_logger
    except Exception as exc:
        logger.error("audit_trail_setup_failed", extra={"error": str(exc)})
        return None


def get_durable_audit_sink() -> SQLiteAuditSink | None:
    """Return the SQLite sink attached to the global logger, if any."""
    from core.observability.audit import get_audit_logger

    for sink in get_audit_logger().sinks:
        if isinstance(sink, SQLiteAuditSink):
            return sink
    return None


class AuditRetentionScheduler:
    """Owns the periodic audit-log retention sweep and its lifecycle."""

    def __init__(
        self,
        retention_days: int,
        interval_seconds: int = _SWEEP_INTERVAL_SECONDS,
    ) -> None:
        self._retention_days = retention_days
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Schedule the sweep loop. Idempotent — a second call is a no-op."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="audit-retention-sweep")
        logger.info(
            "audit_retention_scheduler_started",
            extra={
                "retention_days": self._retention_days,
                "interval_seconds": self._interval,
            },
        )

    async def stop(self) -> None:
        """Cancel the sweep loop and await its teardown. Idempotent."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                sink = get_durable_audit_sink()
                if sink is not None:
                    loop = asyncio.get_running_loop()
                    purged = await loop.run_in_executor(
                        None, sink.purge_older_than, self._retention_days
                    )
                    logger.info("audit_retention_sweep_done", extra={"purged": purged})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("audit_retention_sweep_failed", extra={"error": str(exc)})
            await asyncio.sleep(self._interval)


def start_audit_trail(app: Any) -> None:
    """Configure the audit trail and start its retention sweep at startup.

    Best-effort: stores the scheduler on ``app.state.audit_retention_scheduler``
    (``None`` when not started) so :func:`stop_audit_trail` can tear it down.
    """
    app.state.audit_retention_scheduler = None
    try:
        if configure_audit_logging() is None:
            return
        config = get_audit_config()
        if config.retention_days <= 0 or config.db_path is None:
            # Nothing durable to purge, or an explicit keep-forever policy.
            return
        scheduler = AuditRetentionScheduler(config.retention_days)
        scheduler.start()
        app.state.audit_retention_scheduler = scheduler
    except Exception as exc:
        logger.warning("audit_trail_startup_failed", extra={"error": str(exc)})


async def stop_audit_trail(app: Any) -> None:
    """Stop the audit retention sweep if one was started. Best-effort."""
    scheduler = getattr(app.state, "audit_retention_scheduler", None)
    if scheduler is None:
        return
    try:
        await scheduler.stop()
    except Exception as exc:
        logger.warning("audit_trail_shutdown_failed", extra={"error": str(exc)})


__all__ = [
    "AuditRetentionScheduler",
    "configure_audit_logging",
    "get_durable_audit_sink",
    "start_audit_trail",
    "stop_audit_trail",
]
