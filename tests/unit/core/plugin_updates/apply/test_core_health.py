"""Core health: the new distribution version, the same active plugins, a stable probe."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from baselith.core.config.plugin_update_apply import UpdateApplyConfig
from baselith.core.plugin_updates.apply.boot_report import BootReport, PluginBootState
from baselith.core.plugin_updates.apply.drivers._core_health import (
    CoreExpectation,
    wait_core_healthy,
)
from baselith.core.plugin_updates.apply.store import RunStore

from ._executor_env import Clock

T0 = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
CFG = UpdateApplyConfig(stable_seconds=5, health_timeout_seconds=30)


def _report(
    store: RunStore, *, version: str | None, plugins: dict[str, bool], pid: int = 1
) -> None:
    report = BootReport(
        pid=pid,
        booted_at=T0 + timedelta(seconds=1),
        core_version="1.3.1",
        framework_version=version,
        plugins={
            n: PluginBootState(version="1.0.0", directory="d", active=True, healthy=ok)
            for n, ok in plugins.items()
        },
    )
    store.boot_dir.mkdir(parents=True, exist_ok=True)
    (store.boot_dir / f"{pid}.json").write_text(report.model_dump_json())


async def _wait(store: RunStore, *, status: int = 200, must: list[str] | None = None):
    clock = Clock()

    async def probe() -> int:
        return status

    expect = CoreExpectation(
        run_id="pinstall-20261114T120000Z-0000abcd",
        version="1.15.0",
        restart_at=T0,
        must_stay_active=["auth"] if must is None else must,
    )
    return await wait_core_healthy(
        store, expect, CFG, probe=probe, clock=clock, sleep=clock.sleep
    )


async def test_new_version_with_the_same_plugins_is_healthy(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    _report(store, version="1.15.0", plugins={"auth": True})
    assert (await _wait(store)).ok


async def test_old_framework_version_is_unhealthy(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    _report(store, version="1.14.0", plugins={"auth": True})
    verdict = await _wait(store)
    assert not verdict.ok and "runs 1.14.0, expected 1.15.0" in verdict.reason
    _report(store, version=None, plugins={"auth": True})
    assert "unknown version" in (await _wait(store)).reason


async def test_missing_plugin_is_unhealthy(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    _report(store, version="1.15.0", plugins={})
    verdict = await _wait(store)
    assert not verdict.ok and verdict.reason == "plugins no longer active: auth"


async def test_a_newly_active_plugin_is_unhealthy(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    _report(store, version="1.15.0", plugins={"auth": True, "extra": True})
    assert (await _wait(store)).reason == "plugins newly active: extra"


async def test_unhealthy_plugin_is_unhealthy(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    _report(store, version="1.15.0", plugins={"auth": False})
    assert (await _wait(store)).reason == "plugins reporting unhealthy: auth"


async def test_stable_window_required(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    _report(store, version="1.15.0", plugins={"auth": True})
    verdict = await _wait(store, status=503)
    assert not verdict.ok and verdict.reason == "readiness answered 503"


async def test_no_report_since_the_restart(tmp_path: Path) -> None:
    verdict = await _wait(RunStore(tmp_path))
    assert not verdict.ok and verdict.reason == "no boot report since the restart"
    assert os.getpid() > 0
