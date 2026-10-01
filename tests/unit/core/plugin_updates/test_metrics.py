"""The update-available gauge mirrors the latest check report."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry
from prometheus_client.multiprocess import MultiProcessCollector

from core.observability.metrics import UPDATE_AVAILABLE
from core.plugin_updates.metrics import publish_update_metrics
from core.plugin_updates.models import (
    CheckReport,
    SystemUpdate,
    UpdateCandidate,
)


def _cand(name: str, available: bool) -> UpdateCandidate:
    return UpdateCandidate(
        plugin=name, installed_version="1.0.0", latest=None, available=available
    )


def _report(
    candidates: list[UpdateCandidate], system: SystemUpdate | None = None
) -> CheckReport:
    return CheckReport(
        checked_at=datetime.now(UTC), candidates=candidates, system=system
    )


def _series() -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for metric in UPDATE_AVAILABLE.collect():
        for sample in metric.samples:
            out[(sample.labels["component"], sample.labels["security"])] = sample.value
    return out


def _active() -> dict[tuple[str, str], float]:
    return {k: v for k, v in _series().items() if v}


@pytest.fixture(autouse=True)
def _clean() -> None:
    publish_update_metrics(_report([]))


def test_gauge_reflects_plugin_and_core_updates() -> None:
    system = SystemUpdate(
        repo="o/r", installed_version="1.0.0", available=True, security=True
    )
    publish_update_metrics(_report([_cand("a", True), _cand("b", False)], system))
    assert _active() == {("plugin:a", "false"): 1.0, ("core", "true"): 1.0}
    assert _series()[("plugin:b", "false")] == 0.0


def test_non_security_core_update_is_labelled_false() -> None:
    system = SystemUpdate(repo="o/r", installed_version="1.0.0", available=True)
    publish_update_metrics(_report([], system))
    assert _active() == {("core", "false"): 1.0}


def test_cleared_updates_remove_the_series() -> None:
    system = SystemUpdate(
        repo="o/r", installed_version="1.0.0", available=True, security=True
    )
    publish_update_metrics(_report([_cand("a", True)], system))
    publish_update_metrics(
        _report(
            [_cand("a", False)],
            SystemUpdate(repo="o/r", installed_version="2.0.0"),
        )
    )
    assert _active() == {}
    assert _series()[("plugin:a", "false")] == 0.0
    assert _series()[("core", "true")] == 0.0


def test_security_flip_replaces_the_label_set() -> None:
    publish_update_metrics(
        _report(
            [],
            SystemUpdate(
                repo="o/r", installed_version="1.0.0", available=True, security=True
            ),
        )
    )
    publish_update_metrics(
        _report([], SystemUpdate(repo="o/r", installed_version="1.0.0", available=True))
    )
    assert _active() == {("core", "false"): 1.0}


def test_advisory_without_a_fixed_release_still_fires_security() -> None:
    system = SystemUpdate(
        repo="o/r", installed_version="1.0.0", available=False, security=True
    )
    publish_update_metrics(_report([], system))
    assert _active() == {("core", "true"): 1.0}


def test_no_report_or_no_system_publishes_nothing() -> None:
    publish_update_metrics(_report([_cand("a", True)]))
    publish_update_metrics(None)
    assert _active() == {}
    assert _series()[("plugin:a", "false")] == 0.0


_WRITER = """
import sys
from datetime import UTC, datetime
from core.plugin_updates.metrics import publish_update_metrics
from core.plugin_updates.models import CheckReport, SystemUpdate, UpdateCandidate

available = sys.argv[1] == "1"
publish_update_metrics(CheckReport(
    checked_at=datetime.now(UTC),
    candidates=[UpdateCandidate(
        plugin="a", installed_version="1", latest=None, available=available)],
    system=SystemUpdate(
        repo="o/r", installed_version="1", available=available, security=available),
))
"""


def _write(directory: Path, available: bool) -> None:
    """Publish from a fresh interpreter, i.e. a distinct worker process."""
    env = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(directory)}
    subprocess.run(
        [sys.executable, "-c", _WRITER, "1" if available else "0"],
        check=True,
        env=env,
        cwd=Path(__file__).resolve().parents[4],
        timeout=60,
    )


def _collect(directory: Path) -> dict[tuple[str, str], float]:
    registry = CollectorRegistry()
    MultiProcessCollector(registry, path=str(directory))
    return {
        (s.labels["component"], s.labels["security"]): s.value
        for m in registry.collect()
        if m.name == "baselith_update_available"
        for s in m.samples
    }


def test_multiprocess_publish_then_clear(tmp_path: Path) -> None:
    """The regression that single-process tests cannot see: a cleared update
    must export 0 from the mmap files, not its last 1."""
    _write(tmp_path, True)
    assert _collect(tmp_path) == {
        ("plugin:a", "false"): 1.0,
        ("plugin:a", "true"): 0.0,
        ("core", "true"): 1.0,
        ("core", "false"): 0.0,
    }
    _write(tmp_path, False)
    assert set(_collect(tmp_path).values()) == {0.0}


def test_multiprocess_newer_worker_supersedes_a_dead_workers_one(
    tmp_path: Path,
) -> None:
    """Two pids: the older wrote 1 and was never marked dead, the newer wrote
    0. The newest write wins, so the alert resolves."""
    _write(tmp_path, True)  # first process exits without mark_process_dead
    time.sleep(0.05)
    _write(tmp_path, False)
    assert len(list(tmp_path.glob("gauge_mostrecent_*.db"))) == 2
    collected = _collect(tmp_path)
    assert collected[("core", "true")] == 0.0
    assert collected[("plugin:a", "false")] == 0.0
