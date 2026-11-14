"""Did the restarted API come up on the expected distribution version, and stay up?

The core counterpart of :mod:`..health`: every worker that booted after the
restart must report ``framework_version`` equal to the target, exactly the
plugins that were active before the run must be active (none lost, none
gained: a release that changes the active set is judged by a person, not
here), none of them unhealthy, and readiness must answer 200 for
``stable_seconds`` in a row. Reasons reuse :mod:`..health`'s wording and name
versions and plugins only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from baselith.core.config.plugin_update_apply import UpdateApplyConfig

from ..boot_report import BootReport
from ..health import HealthVerdict, wait_until_healthy, workers_problem
from ..store import RunStore


@dataclass(frozen=True)
class CoreExpectation:
    """What the API must report after a core run's restart."""

    run_id: str
    version: str
    restart_at: datetime
    must_stay_active: list[str] = field(default_factory=list)


def _problem(report: BootReport, expect: CoreExpectation) -> str | None:
    running = report.framework_version
    if running != expect.version:
        return f"the core runs {running or 'an unknown version'}, expected {expect.version}"
    active = {name for name, state in report.plugins.items() if state.active}
    wanted = set(expect.must_stay_active)
    lost = sorted(wanted - active)
    if lost:
        return f"plugins no longer active: {', '.join(lost)}"
    gained = sorted(active - wanted)
    if gained:
        return f"plugins newly active: {', '.join(gained)}"
    sick = sorted(n for n in wanted if report.plugins[n].healthy is False)
    return f"plugins reporting unhealthy: {', '.join(sick)}" if sick else None


def evaluate_core_reports(
    reports: Sequence[BootReport], expect: CoreExpectation
) -> HealthVerdict:
    """Every worker that booted after the restart must agree with ``expect``."""
    waiting = workers_problem(reports)
    if waiting is not None:
        return HealthVerdict(False, waiting)
    for report in reports:
        problem = _problem(report, expect)
        if problem is not None:
            return HealthVerdict(False, problem)
    return HealthVerdict(True)


async def wait_core_healthy(
    store: RunStore,
    expect: CoreExpectation,
    config: UpdateApplyConfig,
    *,
    probe: Callable[[], Awaitable[int]],
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
) -> HealthVerdict:
    """Boot reports agree with ``expect`` and readiness holds, within the deadline."""
    return await wait_until_healthy(
        store,
        expect.restart_at,
        lambda reports: evaluate_core_reports(reports, expect),
        config,
        probe=probe,
        clock=clock,
        sleep=sleep,
    )


__all__ = ["CoreExpectation", "evaluate_core_reports", "wait_core_healthy"]
