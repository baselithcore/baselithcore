"""Finished runs are counted once across web workers; in-flight and failed runs are gauged."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry
from prometheus_client.multiprocess import MultiProcessCollector

from baselith.core.observability.metrics import (
    UPDATE_ROLLBACK_FAILED,
    UPDATE_RUN_INFLIGHT_AGE,
    UPDATE_RUN_TOTAL,
)
from baselith.core.plugin_updates.apply.models import RunKind, RunState
from baselith.core.plugin_updates.apply.store import RunStore
from baselith.core.plugin_updates.outcomes import run_sweeps, sweep

T0 = datetime(2026, 11, 16, 1, 0, tzinfo=UTC)


def _run(store: RunStore, plugin: str = "demo", *, at: datetime = T0) -> str:
    return store.create(
        kind=RunKind.UPDATE,
        plugin=plugin,
        from_version="1.0.0",
        to_version="1.0.1",
        tarball_sha256="a" * 64,
        requested_by="a",
        approval_required=False,
        approval_ttl_seconds=60,
        now=at,
    ).id


def _count(subject: str, outcome: str) -> float:
    value = UPDATE_RUN_TOTAL.labels(subject=subject, outcome=outcome)._value.get()
    return float(value)


def _gauge(gauge: object, subject: str) -> float:
    return float(gauge.labels(subject=subject)._value.get())  # type: ignore[attr-defined]


def test_each_finished_run_is_counted_once(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    before = _count("plugin:demo", "succeeded")
    run_id = _run(store)
    assert sweep(store, now=T0) == 0  # approved: not finished
    store.transition(run_id, RunState.SUCCEEDED, now=T0)
    assert sweep(store, now=T0) == 1
    assert _count("plugin:demo", "succeeded") == before + 1


def test_second_sweep_does_not_recount(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = _run(store, "again")
    store.transition(run_id, RunState.FAILED, now=T0)
    assert sweep(store, now=T0) == 1
    before = _count("plugin:again", "failed")
    assert sweep(store, now=T0) == 0
    assert sweep(RunStore(tmp_path), now=T0) == 0  # a restarted worker
    assert _count("plugin:again", "failed") == before


def test_core_runs_are_labelled_core(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = store.create(
        kind=RunKind.UPDATE,
        plugin="baselith_core",
        subject="core",
        from_version="1.14.0",
        to_version="1.14.1",
        tarball_sha256="c" * 40,
        requested_by="a",
        approval_required=False,
        approval_ttl_seconds=60,
        now=T0,
    )
    before = _count("core", "rolled_back")
    store.transition(run.id, RunState.ROLLED_BACK, now=T0)
    assert sweep(store, now=T0) == 1
    assert _count("core", "rolled_back") == before + 1


def test_inflight_age_is_zero_when_idle(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = _run(store, "slow")
    store.transition(run_id, RunState.HEALTH_CHECKING, now=T0)
    sweep(store, now=T0 + timedelta(minutes=10))
    assert _gauge(UPDATE_RUN_INFLIGHT_AGE, "plugin:slow") == 600.0
    store.transition(run_id, RunState.SUCCEEDED, now=T0 + timedelta(minutes=11))
    sweep(store, now=T0 + timedelta(minutes=12))
    assert _gauge(UPDATE_RUN_INFLIGHT_AGE, "plugin:slow") == 0.0


def test_a_run_waiting_for_approval_is_not_in_flight(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    store.create(
        kind=RunKind.UPDATE,
        plugin="waits",
        from_version="1",
        to_version="2",
        tarball_sha256="a" * 64,
        requested_by="a",
        approval_required=True,
        approval_ttl_seconds=86400,
        now=T0,
    )
    sweep(store, now=T0 + timedelta(hours=5))
    assert _gauge(UPDATE_RUN_INFLIGHT_AGE, "plugin:waits") == 0.0


def test_rollback_failed_gauge_counts_unresolved_runs(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = _run(store, "broken")
    store.transition(run_id, RunState.ROLLBACK_FAILED, now=T0)
    sweep(store, now=T0)
    assert _gauge(UPDATE_ROLLBACK_FAILED, "plugin:broken") == 1.0
    assert _gauge(UPDATE_RUN_INFLIGHT_AGE, "plugin:broken") == 0.0
    store.transition(run_id, RunState.FAILED, now=T0)  # an operator resolved it
    sweep(store, now=T0)
    assert _gauge(UPDATE_ROLLBACK_FAILED, "plugin:broken") == 0.0


def test_unknown_state_file_is_ignored(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = _run(store, "ok")
    store.transition(run_id, RunState.SUCCEEDED, now=T0)
    (tmp_path / "runs" / "pinstall-20261116T010000Z-deadbeef.json").write_text(
        '{"id": "pinstall-20261116T010000Z-deadbeef", "state": "from_the_future"}',
        encoding="utf-8",
    )
    assert sweep(store, now=T0) == 1


async def test_run_sweeps_stops_and_survives_a_failing_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from baselith.core.plugin_updates import outcomes

    calls: list[int] = []
    stop = asyncio.Event()

    def flaky(store: RunStore, *, now: datetime) -> int:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk")
        stop.set()
        return 0

    monkeypatch.setattr(outcomes, "sweep", flaky)
    await asyncio.wait_for(run_sweeps(RunStore(tmp_path), 0.01, stop), timeout=5)
    assert len(calls) == 2


_WORKER = """
import sys
from datetime import UTC, datetime
from pathlib import Path
from baselith.core.plugin_updates.apply.store import RunStore
from baselith.core.plugin_updates.outcomes import sweep

print(sweep(RunStore(Path(sys.argv[1])), now=datetime.now(UTC)))
"""


def _worker(metrics: Path, store: Path) -> int:
    env = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(metrics)}
    out = subprocess.run(
        [sys.executable, "-c", _WORKER, str(store)],
        check=True,
        env=env,
        cwd=Path(__file__).resolve().parents[4],
        timeout=60,
        capture_output=True,
        text=True,
    )
    return int(out.stdout.strip().splitlines()[-1])


def test_two_workers_count_once(tmp_path: Path) -> None:
    metrics, state = tmp_path / "metrics", tmp_path / "state"
    metrics.mkdir()
    store = RunStore(state)
    for name in ("one", "two"):
        store.transition(_run(store, name), RunState.SUCCEEDED, now=T0)
    assert _worker(metrics, state) + _worker(metrics, state) == 2
    registry = CollectorRegistry()
    MultiProcessCollector(registry, path=str(metrics))
    totals = {
        s.labels["subject"]: s.value
        for m in registry.collect()
        if m.name == "baselith_update_run"
        for s in m.samples
        if s.name == "baselith_update_run_total"
    }
    assert totals == {"plugin:one": 1.0, "plugin:two": 1.0}


def test_metering_mark_is_taken_once_and_pruned_with_the_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = _run(store, "marks")
    store.transition(run_id, RunState.SUCCEEDED, now=T0)
    assert store.claim_metering(run_id) and store.is_metered(run_id)
    assert not store.claim_metering(run_id)
    store.release_metering(run_id)
    assert not store.is_metered(run_id)
    store.claim_metering(run_id)
    newer = _run(store, "marks", at=T0 + timedelta(days=1))
    store.transition(newer, RunState.SUCCEEDED, now=T0 + timedelta(days=1))
    later = T0 + timedelta(days=400)  # unaudited retention passed; newest kept
    assert store.prune_finished(later) == [run_id]
    assert not (tmp_path / "metered" / run_id).exists()


async def test_web_tier_sweeps_only_with_one_click_updates_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from baselith.core.api import _runtime_services as rs
    from baselith.core.config import plugin_update_apply as cfg_mod
    from baselith.core.config.plugin_update_apply import UpdateApplyConfig

    app = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(
        cfg_mod, "get_update_apply_config", lambda: UpdateApplyConfig(enabled=False)
    )
    rs._start_update_outcomes(app)
    assert getattr(app.state, "update_outcomes", None) is None
    on = UpdateApplyConfig(enabled=True, state_dir=tmp_path)
    monkeypatch.setattr(cfg_mod, "get_update_apply_config", lambda: on)
    rs._start_update_outcomes(app)
    task, stop = app.state.update_outcomes
    await asyncio.sleep(0.05)
    assert not task.done()
    await rs.stop_runtime_services(app)
    assert task.done() and app.state.update_outcomes is None
