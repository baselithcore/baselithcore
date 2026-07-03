"""
Background retention scheduler.

Periodically runs a retention sweep (DSR ``purge_expired``) so the
storage-limitation principle (GDPR Art. 5(1)(e)) is actually *enforced*, not
merely available on demand. Opt-in: started only when ``PRIVACY_ENABLED`` and
``PRIVACY_RETENTION_DAYS > 0`` (see :mod:`core.api.startup_checks`).

The sweep is global (all tenants) and runs once shortly after startup, then on a
fixed interval. Failures are logged and never kill the loop.
"""

from __future__ import annotations

import asyncio

from core.observability.logging import get_logger
from core.privacy.service import get_data_subject_service

logger = get_logger(__name__)

# Day granularity matches PRIVACY_RETENTION_DAYS; a daily sweep keeps DB load
# negligible while bounding how long expired data lingers to ~24h past horizon.
_DEFAULT_INTERVAL_SECONDS = 24 * 3600


class RetentionScheduler:
    """Owns the periodic retention-sweep task and its lifecycle."""

    def __init__(
        self,
        retention_seconds: int,
        interval_seconds: int = _DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._retention_seconds = retention_seconds
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Schedule the sweep loop. Idempotent — a second call is a no-op."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="privacy-retention-sweep")
        logger.info(
            "privacy_retention_scheduler_started",
            extra={
                "retention_seconds": self._retention_seconds,
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
                report = await get_data_subject_service().purge_expired(
                    self._retention_seconds
                )
                logger.info(
                    "privacy_retention_sweep_done",
                    extra={"purged": report.total},
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "privacy_retention_sweep_failed", extra={"error": str(exc)}
                )
            await asyncio.sleep(self._interval)
