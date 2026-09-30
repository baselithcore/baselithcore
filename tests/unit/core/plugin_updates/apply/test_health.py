from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.config.plugin_update_apply import UpdateApplyConfig
from core.plugin_updates.apply.boot_report import BootReport, PluginBootState
from core.plugin_updates.apply.health import evaluate_boot_reports, wait_healthy
from core.plugin_updates.apply.models import Expectation
from core.plugin_updates.apply.store import RunStore

T0 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
EXPECT = Expectation(
    run_id="pinstall-20260930T120000Z-0000abcd",
    plugin="demo",
    version="1.2.0",
    store_dir="demo-1.2.0",
    restart_at=T0,
    must_stay_active=["auth", "wiki"],
)
OVERLAY = "/ov/.store/demo-1.2.0"


def _demo(**kw: object) -> PluginBootState:
    base: dict[str, object] = {
        "version": "1.2.0",
        "directory": OVERLAY,
        "active": True,
        "healthy": True,
    }
    return PluginBootState.model_validate(base | kw)


def _report(pid: int = 1, workers: int = 1, **plugins: PluginBootState) -> BootReport:
    base = {
        "demo": _demo(),
        "auth": PluginBootState(
            version="3.0.0", directory="/app/plugins/auth", active=True
        ),
        "wiki": PluginBootState(
            version="1.0.0", directory="/app/plugins/wiki", active=True
        ),
    }
    return BootReport(
        pid=pid,
        workers=workers,
        booted_at=T0 + timedelta(seconds=3),
        core_version="1.50.0",
        plugins=base | plugins,
    )


def test_all_good() -> None:
    assert evaluate_boot_reports([_report()], EXPECT).ok


def test_no_report_is_not_ok() -> None:
    assert not evaluate_boot_reports([], EXPECT).ok


def test_bundled_copy_loaded_instead_of_overlay() -> None:
    bad = _report(demo=_demo(version="1.1.0", directory="/app/plugins/demo"))
    verdict = evaluate_boot_reports([bad], EXPECT)
    assert not verdict.ok and "demo" in verdict.reason


def test_dependent_plugin_lost_is_unhealthy() -> None:
    lost = _report(
        wiki=PluginBootState(version="1.0.0", directory="/x/wiki", active=False)
    )
    verdict = evaluate_boot_reports([lost], EXPECT)
    assert not verdict.ok and "wiki" in verdict.reason


def test_one_bad_worker_fails_all() -> None:
    assert not evaluate_boot_reports(
        [_report(1), _report(2, demo=_demo(healthy=False))], EXPECT
    ).ok


def test_unknown_health_is_not_healthy() -> None:
    assert not evaluate_boot_reports([_report(demo=_demo(healthy=None))], EXPECT).ok


def test_dependent_unhealthy_fails() -> None:
    bad = PluginBootState(
        version="1.0.0", directory="/x/wiki", active=True, healthy=False
    )
    assert not evaluate_boot_reports([_report(wiki=bad)], EXPECT).ok


def test_wrong_version_in_overlay_fails() -> None:
    assert not evaluate_boot_reports([_report(demo=_demo(version="1.1.9"))], EXPECT).ok


def test_wrong_store_dir_fails() -> None:
    assert not evaluate_boot_reports(
        [_report(demo=_demo(directory="/ov/.store/demo-1.1.0"))], EXPECT
    ).ok


def test_reason_never_leaks_paths() -> None:
    cases = [
        _report(demo=_demo(directory="/secret/host/demo")),
        _report(demo=_demo(version="9", directory="/secret/host/.store/demo-1.2.0")),
        _report(demo=_demo(healthy=False, directory="/secret/host/x")),
    ]
    for bad in cases:
        reason = evaluate_boot_reports([bad], EXPECT).reason
        assert reason and "/" not in reason and "secret" not in reason


def test_rollback_to_bundled_expects_no_store_dir() -> None:
    back = EXPECT.model_copy(update={"version": "1.1.0", "store_dir": None})
    ok = _report(demo=_demo(version="1.1.0", directory="/app/plugins/demo"))
    assert evaluate_boot_reports([ok], back).ok
    still = _report(demo=_demo(version="1.1.0"))
    assert not evaluate_boot_reports([still], back).ok


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds


def _store(tmp_path: Path, *reports: BootReport) -> RunStore:
    store = RunStore(tmp_path)
    store.boot_dir.mkdir(parents=True, exist_ok=True)
    for r in reports or (_report(),):
        (store.boot_dir / f"{r.pid}.json").write_text(r.model_dump_json())
    return store


async def test_stable_readiness_passes(tmp_path: Path) -> None:
    clock = _Clock()

    async def probe() -> int:
        return 200

    verdict = await wait_healthy(
        _store(tmp_path),
        EXPECT,
        UpdateApplyConfig(stable_seconds=20),
        probe=probe,
        clock=clock,
        sleep=clock.sleep,
    )
    assert verdict.ok and clock.t >= 20


async def test_flapping_ready_is_unhealthy(tmp_path: Path) -> None:
    clock = _Clock()
    answers = iter(([200] * 10 + [503]) * 100)

    async def probe() -> int:
        return next(answers)

    cfg = UpdateApplyConfig(stable_seconds=20, health_timeout_seconds=60)
    verdict = await wait_healthy(
        _store(tmp_path), EXPECT, cfg, probe=probe, clock=clock, sleep=clock.sleep
    )
    assert not verdict.ok and "readiness" in verdict.reason


async def test_probe_errors_count_as_down(tmp_path: Path) -> None:
    clock = _Clock()

    async def probe() -> int:
        raise OSError("connection refused")

    cfg = UpdateApplyConfig(health_timeout_seconds=30)
    verdict = await wait_healthy(
        _store(tmp_path), EXPECT, cfg, probe=probe, clock=clock, sleep=clock.sleep
    )
    assert not verdict.ok and "readiness" in verdict.reason
    assert "refused" not in verdict.reason


async def test_late_worker_report_resets_stability(tmp_path: Path) -> None:
    """A second worker booting wrong mid-window must fail the run."""
    clock = _Clock()
    store = _store(tmp_path)

    async def probe() -> int:
        if clock.t >= 5:
            bad = _report(2, demo=_demo(version="1.1.0", directory="/app/plugins/demo"))
            (store.boot_dir / "2.json").write_text(bad.model_dump_json())
        return 200

    cfg = UpdateApplyConfig(stable_seconds=20, health_timeout_seconds=60)
    verdict = await wait_healthy(
        store, EXPECT, cfg, probe=probe, clock=clock, sleep=clock.sleep
    )
    assert not verdict.ok and "demo" in verdict.reason


async def test_no_report_times_out(tmp_path: Path) -> None:
    clock = _Clock()

    async def probe() -> int:
        return 200

    store = RunStore(tmp_path)
    cfg = UpdateApplyConfig(health_timeout_seconds=30)
    verdict = await wait_healthy(
        store, EXPECT, cfg, probe=probe, clock=clock, sleep=clock.sleep
    )
    assert not verdict.ok and "boot report" in verdict.reason
    assert clock.t <= 32


def test_partial_restart_is_not_ok() -> None:
    verdict = evaluate_boot_reports([_report(1, workers=2)], EXPECT)
    assert not verdict.ok and "1 of 2 workers reported" in verdict.reason


def test_all_declared_workers_reported_is_ok() -> None:
    assert evaluate_boot_reports(
        [_report(1, workers=2), _report(2, workers=2)], EXPECT
    ).ok


def test_same_pid_twice_does_not_fake_a_worker() -> None:
    assert not evaluate_boot_reports(
        [_report(1, workers=2), _report(1, workers=2)], EXPECT
    ).ok


def test_stale_report_beside_a_good_one_is_ignored(tmp_path: Path) -> None:
    from core.plugin_updates.apply.boot_report import read_boot_reports

    store = _store(tmp_path)
    stale = _report(9, demo=_demo(version="0.1.0", directory="/app/plugins/demo"))
    stale = stale.model_copy(update={"booted_at": T0 - timedelta(seconds=30)})
    (store.boot_dir / "9.json").write_text(stale.model_dump_json())
    assert evaluate_boot_reports(read_boot_reports(store, since=T0), EXPECT).ok


async def test_partial_restart_never_becomes_healthy(tmp_path: Path) -> None:
    clock = _Clock()

    async def probe() -> int:
        return 200

    cfg = UpdateApplyConfig(stable_seconds=20, health_timeout_seconds=40)
    verdict = await wait_healthy(
        _store(tmp_path, _report(1, workers=2)),
        EXPECT,
        cfg,
        probe=probe,
        clock=clock,
        sleep=clock.sleep,
    )
    assert not verdict.ok and "1 of 2 workers reported" in verdict.reason


async def test_second_worker_arriving_makes_it_healthy(tmp_path: Path) -> None:
    clock = _Clock()
    store = _store(tmp_path, _report(1, workers=2))

    async def probe() -> int:
        if clock.t >= 5:
            (store.boot_dir / "2.json").write_text(
                _report(2, workers=2).model_dump_json()
            )
        return 200

    cfg = UpdateApplyConfig(stable_seconds=20, health_timeout_seconds=60)
    assert (
        await wait_healthy(
            store, EXPECT, cfg, probe=probe, clock=clock, sleep=clock.sleep
        )
    ).ok


async def test_hanging_probe_is_bounded_by_the_deadline(tmp_path: Path) -> None:
    async def probe() -> int:
        await asyncio.sleep(3600)
        return 200

    # model_copy skips validation: the real minimum (30 s) is too slow for a real-time test
    cfg = UpdateApplyConfig().model_copy(update={"health_timeout_seconds": 1})
    verdict = await wait_healthy(
        _store(tmp_path), EXPECT, cfg, probe=probe, clock=_mono, sleep=_real_sleep
    )
    assert not verdict.ok and "unreachable" in verdict.reason


def _mono() -> float:
    import time

    return time.monotonic()


async def _real_sleep(seconds: float) -> None:
    await asyncio.sleep(min(seconds, 0.05))
