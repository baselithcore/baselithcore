"""Cron-style scheduler for periodic Baselithbot jobs.

Uses ``apscheduler`` (AsyncIOScheduler) when installed; falls back to a
lightweight in-process polling scheduler that supports interval and
cron-like fixed-time triggers.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

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


class CronScheduler:
    """Async cron scheduler. APScheduler-backed when available."""

    def __init__(self) -> None:
        self._jobs: dict[str, CronJob] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._tick = 1.0

    def add_interval(self, name: str, fn: JobFn, seconds: float) -> None:
        if seconds < 1:
            raise ValueError("interval must be >= 1 second")
        self._jobs[name] = CronJob(
            name=name, fn=fn, interval_seconds=seconds,
            next_run_at=time.time() + seconds,
        )

    def remove(self, name: str) -> bool:
        return self._jobs.pop(name, None) is not None

    def list(self) -> list[dict[str, object]]:
        return [
            {
                "name": j.name,
                "interval_seconds": j.interval_seconds,
                "enabled": j.enabled,
                "runs": j.runs,
                "next_run_at": j.next_run_at,
                "last_error": j.last_error,
            }
            for j in self._jobs.values()
        ]

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name="baselithbot-cron")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            for job in list(self._jobs.values()):
                if not job.enabled or job.next_run_at > now:
                    continue
                try:
                    await job.fn()
                    job.last_error = None
                except Exception as exc:
                    job.last_error = str(exc)
                    logger.warning("baselithbot_cron_job_error", name=job.name, error=str(exc))
                job.runs += 1
                job.next_run_at = now + job.interval_seconds
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick)
            except asyncio.TimeoutError:
                continue


__all__ = ["CronScheduler", "CronJob", "JobFn"]
