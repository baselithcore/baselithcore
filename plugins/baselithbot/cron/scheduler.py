"""Cron-style scheduler for periodic Baselithbot jobs.

Lightweight in-process asyncio scheduler with interval triggers, pause/resume,
manual run-now, and per-job interval adjustment. Does not depend on
``apscheduler``; backend label is always ``"interval"``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from core.observability.logging import get_logger

logger = get_logger(__name__)

JobFn = Callable[[], Awaitable[None]]


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


class CronScheduler:
    """Async interval scheduler."""

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
            job.next_run_at = time.time() + job.interval_seconds
        self._wake.set()
        return True

    def set_interval(self, name: str, seconds: float) -> bool:
        if seconds < 1:
            raise ValueError("interval must be >= 1 second")
        job = self._jobs.get(name)
        if job is None:
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

    def list(self) -> list[dict[str, object]]:
        return [
            {
                "name": j.name,
                "interval_seconds": j.interval_seconds,
                "enabled": j.enabled,
                "runs": j.runs,
                "next_run_at": j.next_run_at,
                "last_run_at": j.last_run_at,
                "last_error": j.last_error,
                "description": j.description,
            }
            for j in self._jobs.values()
        ]

    def get(self, name: str) -> dict[str, object] | None:
        job = self._jobs.get(name)
        if job is None:
            return None
        return {
            "name": job.name,
            "interval_seconds": job.interval_seconds,
            "enabled": job.enabled,
            "runs": job.runs,
            "next_run_at": job.next_run_at,
            "last_run_at": job.last_run_at,
            "last_error": job.last_error,
            "description": job.description,
        }

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
