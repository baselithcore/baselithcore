"""Run outcomes as Prometheus series, swept from the run store by the web tier.

The updater process is not scraped, so the API workers read what it wrote:
:func:`sweep` counts each finished run once in ``baselith_update_run_total``
(the store's ``metered/<id>`` mark decides who counts it, across workers and
restarts) and sets two gauges per subject from the runs still holding a claim:
``baselith_update_run_inflight_age_seconds`` (seconds since an executing run
last changed state; 0 when none) and ``baselith_update_rollback_failed_runs``
(runs waiting for an operator). Every subject on record gets both gauges on
every sweep, so a finished run's series returns to 0 in every worker's file.

Labels are the subject (``core`` or ``plugin:<name>``) and the terminal state:
names and codes only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from baselith.core.observability.metrics import (
    UPDATE_ROLLBACK_FAILED,
    UPDATE_RUN_INFLIGHT_AGE,
    UPDATE_RUN_TOTAL,
)

from .apply.models import RELEASING_STATES, RunState
from .apply.store import RunStore

logger = logging.getLogger(__name__)

#: Terminal outcomes a run is counted under (``rollback_failed`` keeps its claim).
FINISHED_STATES = RELEASING_STATES | {RunState.ROLLBACK_FAILED}
#: Unfinished states that wait on a person, not on the updater: never "stuck".
_WAITING = frozenset(
    {
        RunState.AWAITING_APPROVAL,
        RunState.PR_OPEN,
        RunState.AWAITING_MERGE,
        RunState.ROLLBACK_FAILED,
    }
)


def _count_finished(store: RunStore) -> tuple[int, set[str]]:
    counted = 0
    subjects: set[str] = set()
    for run in store.runs():
        subjects.add(run.subject_key)
        if run.state not in FINISHED_STATES or store.is_metered(run.id):
            continue
        if not store.claim_metering(run.id):
            continue  # another worker counted it
        try:
            UPDATE_RUN_TOTAL.labels(
                subject=run.subject_key, outcome=run.state.value
            ).inc()
        except Exception:
            store.release_metering(run.id)  # the next sweep counts it
            raise
        counted += 1
    return counted, subjects


def sweep(store: RunStore, *, now: datetime) -> int:
    """Count newly finished runs and refresh the gauges; the number counted."""
    counted, subjects = _count_finished(store)
    age: dict[str, float] = dict.fromkeys(subjects, 0.0)
    failed: dict[str, float] = dict.fromkeys(subjects, 0.0)
    for run in store.unfinished():
        key = run.subject_key
        age.setdefault(key, 0.0)
        failed.setdefault(key, 0.0)
        if run.state is RunState.ROLLBACK_FAILED:
            failed[key] += 1
        elif run.state not in _WAITING:
            seconds = max((now - run.updated_at).total_seconds(), 0.0)
            age[key] = max(age[key], seconds)
    for key, value in age.items():
        UPDATE_RUN_INFLIGHT_AGE.labels(subject=key).set(value)
    for key, value in failed.items():
        UPDATE_ROLLBACK_FAILED.labels(subject=key).set(value)
    return counted


async def run_sweeps(store: RunStore, interval: float, stop: asyncio.Event) -> None:
    """:func:`sweep` every ``interval`` seconds until ``stop``; never raises."""
    while not stop.is_set():
        try:
            await asyncio.to_thread(sweep, store, now=datetime.now(UTC))
        except Exception as exc:  # metrics are best effort: keep sweeping
            logger.warning("update_outcomes_sweep_failed error=%s", type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), interval)


__all__ = ["FINISHED_STATES", "run_sweeps", "sweep"]
