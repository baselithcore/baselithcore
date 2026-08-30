"""Cron-style scheduler for periodic Baselithbot jobs.

Lightweight in-process asyncio scheduler with interval and cron-expression
triggers, pause/resume, manual run-now, and per-job interval adjustment.
Cron jobs use :class:`core.task_queue.cron.CronExpression` (UTC). Does not
depend on ``apscheduler``; backend label is always ``"interval"``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from core.observability.logging import get_logger
from core.task_queue.cron import CronExpression

logger = get_logger(__name__)

JobFn = Callable[[], Awaitable[None]]


def _next_cron_ts(expr: CronExpression) -> float:
    """Return the next fire time of a cron expression as an epoch second."""
    return expr.next_after(datetime.now(UTC)).timestamp()


@dataclass
class CronJob:
    name: str
    fn: JobFn
    interval_seconds: float
    next_run_at: float = field(default_factory=time.time)
    enabled: bool = True
    runs: int = 0
    last_error: str | None = None
    last_run_at: float | None = None
    description: str = ""
    # Cron-triggered jobs: raw expression plus its parsed form. When set,
    # ``next_run_at`` is derived from the cron (UTC), not from the interval.
    cron: str | None = None
    cron_expr: CronExpression | None = None


class CronScheduler:
    """Async interval/cron scheduler."""

    BACKEND: str = "interval"

    def __init__(self) -> None:
        self._jobs: dict[str, CronJob] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._tick = 1.0

    @property
    def backend(self) -> str:
        return self.BACKEND

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def add_interval(
        self,
        name: str,
        fn: JobFn,
        seconds: float,
        *,
        description: str = "",
        enabled: bool = True,
    ) -> None:
        if seconds < 1:
            raise ValueError("interval must be >= 1 second")
        self._jobs[name] = CronJob(
            name=name,
            fn=fn,
            interval_seconds=float(seconds),
            next_run_at=time.time() + seconds,
            enabled=enabled,
            description=description,
        )
        self._wake.set()

    def add_cron(
        self,
        name: str,
        expr: str,
        fn: JobFn,
        *,
        description: str = "",
        enabled: bool = True,
    ) -> None:
        """Register a job fired on a 5-field cron expression (UTC).

        Args:
            name: Unique job name (replaces an existing job of the same name).
            expr: Cron expression, e.g. ``"0 12 * * *"``.
            fn: Async zero-argument callable to run.
            description: Human-readable description for the dashboard.
            enabled: Whether the job starts enabled.

        Raises:
            ValueError: If ``expr`` is not a valid cron expression.
        """
        cron_expr = CronExpression.parse(expr)
        self._jobs[name] = CronJob(
            name=name,
            fn=fn,
            interval_seconds=0.0,
            next_run_at=_next_cron_ts(cron_expr),
            enabled=enabled,
            description=description,
            cron=expr,
            cron_expr=cron_expr,
        )
        self._wake.set()

    def remove(self, name: str) -> bool:
        existed = self._jobs.pop(name, None) is not None
        if existed:
            self._wake.set()
        return existed

    def set_enabled(self, name: str, enabled: bool) -> bool:
        job = self._jobs.get(name)
        if job is None:
            return False
        job.enabled = enabled
        if enabled:
            if job.cron_expr is not None:
                job.next_run_at = _next_cron_ts(job.cron_expr)
            else:
                job.next_run_at = time.time() + job.interval_seconds
        self._wake.set()
        return True

    def set_interval(self, name: str, seconds: float) -> bool:
        if seconds < 1:
            raise ValueError("interval must be >= 1 second")
        job = self._jobs.get(name)
        if job is None or job.cron_expr is not None:
            # Cron jobs are driven by their expression, not an interval.
            return False
        job.interval_seconds = float(seconds)
        job.next_run_at = time.time() + seconds
        self._wake.set()
        return True

    def trigger(self, name: str) -> bool:
        """Mark a job as due so the loop runs it on the next tick."""
        job = self._jobs.get(name)
        if job is None:
            return False
        job.next_run_at = time.time()
        self._wake.set()
        return True

    def _job_info(self, job: CronJob) -> dict[str, object]:
        return {
            "name": job.name,
            "interval_seconds": job.interval_seconds,
            "enabled": job.enabled,
            "runs": job.runs,
            "next_run_at": job.next_run_at,
            "last_run_at": job.last_run_at,
            "last_error": job.last_error,
            "description": job.description,
            "cron": job.cron,
        }

    def list(self) -> list[dict[str, object]]:
        return [self._job_info(j) for j in self._jobs.values()]

    def get(self, name: str) -> dict[str, object] | None:
        job = self._jobs.get(name)
        if job is None:
            return None
        return self._job_info(job)

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._wake.clear()
        self._task = asyncio.create_task(self._run_loop(), name="baselithbot-cron")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            try:
                await self._task
            finally:
                self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            due: list[CronJob] = [
                j for j in list(self._jobs.values()) if j.enabled and j.next_run_at <= now
            ]
            for job in due:
                try:
                    await job.fn()
                    job.last_error = None
                except Exception as exc:
                    job.last_error = str(exc)
                    logger.warning("baselithbot_cron_job_error", name=job.name, error=str(exc))
                job.runs += 1
                job.last_run_at = time.time()
                if job.cron_expr is not None:
                    job.next_run_at = _next_cron_ts(job.cron_expr)
                else:
                    job.next_run_at = job.last_run_at + job.interval_seconds

            sleep_for = self._sleep_until_next(now=time.time())
            self._wake.clear()
            stop_wait = asyncio.create_task(self._stop.wait())
            wake_wait = asyncio.create_task(self._wake.wait())
            try:
                _, pending = await asyncio.wait(
                    {stop_wait, wake_wait},
                    timeout=sleep_for,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
            except asyncio.CancelledError:
                stop_wait.cancel()
                wake_wait.cancel()
                raise

    def _sleep_until_next(self, *, now: float) -> float:
        active = [j.next_run_at - now for j in self._jobs.values() if j.enabled]
        if not active:
            return self._tick
        return max(0.05, min(self._tick, min(active)))


__all__ = ["CronScheduler", "CronJob", "JobFn"]
