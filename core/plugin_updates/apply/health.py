"""Did the restarted API come up on the expected plugin version, and stay up?

Two independent signals must agree: every worker that booted after the restart
wrote a boot report showing the expected release, and ``/health/ready`` kept
answering 200 for ``stable_seconds`` in a row. Reasons carry plugin names and
versions only, never host paths.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from core.config.plugin_update_apply import UpdateApplyConfig
from core.plugins.overlay import STORE_DIRNAME

from .boot_report import BootReport, read_boot_reports
from .models import Expectation
from .store import RunStore

logger = logging.getLogger(__name__)

_POLL_SECONDS = 1.0
_NO_REPORT = "no boot report since the restart"


@dataclass(frozen=True)
class HealthVerdict:
    """Outcome of a health check; ``reason`` explains a failure."""

    ok: bool
    reason: str = ""


def _problem(report: BootReport, expect: Expectation) -> str | None:
    name = expect.plugin
    state = report.plugins.get(name)
    if state is None or not state.active:
        return f"{name} not active in a restarted worker"
    directory = PurePosixPath(state.directory or "")
    in_store = directory.parent.name == STORE_DIRNAME
    if expect.store_dir is not None and not (
        in_store and directory.name == expect.store_dir
    ):
        return f"{name} was not loaded from the expected release directory"
    if expect.store_dir is None and in_store:
        return f"{name} is still loaded from the overlay"
    if expect.version is not None and state.version != expect.version:
        return f"{name} runs {state.version}, expected {expect.version}"
    if state.healthy is not True:  # False = failing, None = unknown: both fail closed
        return f"{name} health is not confirmed"
    lost = [
        p
        for p in expect.must_stay_active
        if not (report.plugins.get(p) and report.plugins[p].active)
    ]
    if lost:
        return f"plugins no longer active: {', '.join(lost)}"
    sick = [
        p
        for p in expect.must_stay_active
        if p in report.plugins and report.plugins[p].healthy is False
    ]
    return f"plugins reporting unhealthy: {', '.join(sick)}" if sick else None


def evaluate_boot_reports(
    reports: Sequence[BootReport], expect: Expectation
) -> HealthVerdict:
    """Every worker that booted after the restart must agree with ``expect``."""
    if not reports:
        return HealthVerdict(False, _NO_REPORT)
    declared = max(r.workers for r in reports)
    seen = len({r.pid for r in reports})
    if seen < declared:
        return HealthVerdict(False, f"{seen} of {declared} workers reported")
    for report in reports:
        problem = _problem(report, expect)
        if problem is not None:
            return HealthVerdict(False, problem)
    return HealthVerdict(True)


async def wait_healthy(
    store: RunStore,
    expect: Expectation,
    config: UpdateApplyConfig,
    *,
    probe: Callable[[], Awaitable[int]],
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
) -> HealthVerdict:
    """Boot reports agree AND readiness answers 200 for ``stable_seconds`` in a row.

    Reports are re-read on every poll, so a worker that boots late (or wrong)
    inside the window resets stability. Gives up after ``health_timeout_seconds``.
    """
    deadline = clock() + config.health_timeout_seconds
    stable_since: float | None = None
    reason = _NO_REPORT
    while True:
        reports = await asyncio.to_thread(
            read_boot_reports, store, since=expect.restart_at
        )
        verdict = evaluate_boot_reports(reports, expect)
        try:
            status = await asyncio.wait_for(probe(), max(0.1, deadline - clock()))
        except Exception as exc:  # refused, hung (timeout) etc. is simply "not ready"
            logger.debug("plugin_update_probe_failed error=%s", type(exc).__name__)
            status = 0
        if verdict.ok and status == 200:
            now = clock()
            stable_since = now if stable_since is None else stable_since
            if now - stable_since >= config.stable_seconds:
                return HealthVerdict(True)
        else:
            if not verdict.ok:
                reason = verdict.reason
            elif stable_since is not None:
                reason = (
                    f"readiness dropped to {status or 'unreachable'} after coming up"
                )
            else:
                reason = f"readiness answered {status or 'unreachable'}"
            stable_since = None
        if clock() >= deadline:
            return HealthVerdict(False, reason)
        await sleep(_POLL_SECONDS)


__all__ = ["HealthVerdict", "evaluate_boot_reports", "wait_healthy"]
