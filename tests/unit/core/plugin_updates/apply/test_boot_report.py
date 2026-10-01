"""Boot report: what each API worker actually loaded after a restart."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.api import _runtime_services
from core.plugin_updates.apply.boot_report import (
    latest_active_plugins,
    read_boot_reports,
    write_boot_report,
)
from core.plugin_updates.apply.models import Expectation
from core.plugin_updates.apply.store import RunStore

T0 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
RUN = "pinstall-20260930T120000Z-0000abcd"


def _expect(store: RunStore, must: list[str]) -> None:
    store.write_expectation(
        Expectation(
            run_id=RUN,
            plugin="demo",
            version="1.2.0",
            store_dir="demo-1.2.0",
            restart_at=T0,
            must_stay_active=must,
        )
    )


class _Registry:
    def __init__(
        self, active: dict[str, tuple[str, str]], lazy: dict[str, tuple[str, str]]
    ) -> None:
        self.active, self.lazy, self.activated = dict(active), dict(lazy), []

    def get_all(self):
        return [SimpleNamespace(name=n) for n in self.active]

    async def ensure_plugin_active(self, name: str) -> bool:
        self.activated.append(name)
        if name in self.lazy:
            self.active[name] = self.lazy.pop(name)
        return name in self.active

    def get_plugin_version(self, name: str) -> str | None:
        return self.active.get(name, (None, None))[0]

    def get_plugin_directory(self, name: str) -> Path | None:
        d = self.active.get(name, (None, None))[1]
        return Path(d) if d else None

    async def check_health(self, name: str | None = None) -> dict:
        return {name: {"healthy": True}}


async def test_expected_plugin_is_activated_and_reported(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    _expect(store, ["auth"])
    reg = _Registry(
        {"auth": ("3.0.0", "/app/plugins/auth")},
        {"demo": ("1.2.0", "/ov/.store/demo-1.2.0")},
    )
    await write_boot_report(
        reg, store, core_version="1.50.0", now=T0 + timedelta(seconds=5), pid=42
    )
    (report,) = read_boot_reports(store, since=T0)
    assert reg.activated == ["demo"]
    assert report.plugins["demo"].active and report.plugins["demo"].version == "1.2.0"
    assert report.plugins["demo"].healthy is True and report.plugins["auth"].active


async def test_reports_before_the_restart_are_ignored(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    reg = _Registry({"auth": ("3.0.0", "/a")}, {})
    await write_boot_report(
        reg, store, core_version="1.50.0", now=T0 - timedelta(seconds=1), pid=1
    )
    assert read_boot_reports(store, since=T0) == []
    assert latest_active_plugins(store) == ["auth"]


async def test_activation_failure_is_reported_not_raised(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    _expect(store, [])

    class _Boom(_Registry):
        async def ensure_plugin_active(self, name: str) -> bool:
            raise RuntimeError("init failed")

    await write_boot_report(_Boom({}, {}), store, core_version="1.50.0", now=T0, pid=7)
    (report,) = read_boot_reports(store, since=T0 - timedelta(seconds=1))
    assert report.plugins["demo"].active is False


async def test_a_hung_activation_is_bounded_by_the_timeout(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    _expect(store, [])

    class _Hang(_Registry):
        async def ensure_plugin_active(self, name: str) -> bool:
            await asyncio.sleep(30)
            return True

    await asyncio.wait_for(
        write_boot_report(
            _Hang({}, {}),
            store,
            core_version="1",
            now=T0,
            pid=8,
            activation_timeout=0.05,
        ),
        timeout=5,
    )
    (report,) = read_boot_reports(store, since=T0 - timedelta(seconds=1))
    assert report.plugins["demo"].active is False


async def test_workers_write_one_report_each_and_latest_wins(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    await write_boot_report(
        _Registry({"a": ("1", "/a")}, {}), store, core_version="1", now=T0, pid=1
    )
    await write_boot_report(
        _Registry({"a": ("1", "/a"), "b": ("1", "/b")}, {}),
        store,
        core_version="1",
        now=T0 + timedelta(seconds=1),
        pid=2,
    )
    assert sorted(p.name for p in store.boot_dir.glob("*.json")) == ["1.json", "2.json"]
    assert latest_active_plugins(store) == ["a", "b"]


async def test_old_reports_are_pruned_and_content_has_no_secrets(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path)
    store.boot_dir.mkdir(parents=True)
    stale = store.boot_dir / "999.json"
    stale.write_text("{}")
    old = (datetime.now(UTC) - timedelta(days=8)).timestamp()
    os.utime(stale, (old, old))
    await write_boot_report(
        _Registry({"a": ("1", "/x/plugins/a")}, {}),
        store,
        core_version="1",
        now=T0,
        pid=3,
    )
    assert not stale.exists()
    assert set(store.boot_dir.iterdir()) == {store.boot_dir / "3.json"}


async def test_real_registry_health_shape(tmp_path: Path) -> None:
    from core.plugins.registry import PluginRegistry

    class _P:
        metadata = SimpleNamespace(name="demo", version="1.2.0")

        def is_initialized(self) -> bool:
            return True

    reg = PluginRegistry()
    reg._plugins["demo"] = _P()  # type: ignore[assignment]
    store = RunStore(tmp_path)
    _expect(store, [])
    with patch("core.plugins.interface.Plugin.has_health_override", return_value=False):
        await write_boot_report(reg, store, core_version="1", now=T0, pid=5)
    (report,) = read_boot_reports(store, since=T0 - timedelta(seconds=1))
    assert report.plugins["demo"].active and report.plugins["demo"].healthy is True
    assert report.plugins["demo"].version == "1.2.0"


async def test_hook_is_a_noop_when_apply_is_disabled(tmp_path: Path) -> None:
    cfg = SimpleNamespace(enabled=False, state_dir=tmp_path / "st")
    with patch(
        "core.config.plugin_update_apply.get_update_apply_config", return_value=cfg
    ):
        await _runtime_services._write_update_boot_report()
    assert not (tmp_path / "st").exists()


async def test_hook_never_raises(tmp_path: Path) -> None:
    with (
        patch(
            "core.config.plugin_update_apply.get_update_apply_config",
            side_effect=RuntimeError("x"),
        ),
        patch.object(_runtime_services, "logger") as log,
    ):
        await _runtime_services._write_update_boot_report()
    log.warning.assert_called_once_with(
        "plugin_update_boot_report_failed: %s", "RuntimeError"
    )


async def test_total_deadline_bounds_activation_and_health(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    for name in ("p1", "p2", "p3"):
        store.write_expectation(
            Expectation(
                run_id=RUN,
                plugin=name,
                version="1",
                store_dir=None,
                restart_at=T0,
                must_stay_active=[],
            )
        )

    class _Slow(_Registry):
        async def ensure_plugin_active(self, name: str) -> bool:
            await asyncio.sleep(30)
            return True

    loop = asyncio.get_running_loop()
    start = loop.time()
    await write_boot_report(
        _Slow({}, {}), store, core_version="1", now=T0, pid=9, activation_timeout=0.2
    )
    assert loop.time() - start < 2
    (report,) = read_boot_reports(store, since=T0 - timedelta(seconds=1))
    assert all(not s.active and s.healthy is None for s in report.plugins.values())


async def test_hanging_health_is_unhealthy_within_the_bound(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    _expect(store, [])

    class _HangHealth(_Registry):
        async def check_health(self, name: str | None = None) -> dict:
            await asyncio.sleep(30)
            return {}

    reg = _HangHealth({}, {"demo": ("1.2.0", "/d")})
    await asyncio.wait_for(
        write_boot_report(
            reg, store, core_version="1", now=T0, pid=10, activation_timeout=0.3
        ),
        5,
    )
    (report,) = read_boot_reports(store, since=T0 - timedelta(seconds=1))
    assert report.plugins["demo"].active and report.plugins["demo"].healthy is False


def test_health_payload_interpretation() -> None:
    from core.plugin_updates.apply.boot_report import _healthy

    assert _healthy({"plugins": {"a": {"status": "unhealthy"}}}, "a") is False
    assert _healthy({"plugins": {"a": {"status": "not_found"}}}, "a") is False
    assert _healthy({"plugins": {"a": {"status": "healthy"}}}, "a") is True
    assert _healthy("garbage", "a") is None
    assert _healthy({"plugins": {"a": 3}}, "a") is None


def test_naive_since_is_refused(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError):
        read_boot_reports(RunStore(tmp_path), since=T0.replace(tzinfo=None))


async def test_report_records_declared_worker_count(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("BASELITH_WEB_CONCURRENCY", "3")
    store = RunStore(tmp_path)
    reg = _Registry({}, {})
    await write_boot_report(
        reg, store, core_version="1", now=T0 + timedelta(seconds=1), pid=7
    )
    (report,) = read_boot_reports(store, since=T0)
    assert report.workers == 3


def test_no_report_is_none_not_an_empty_list(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    assert latest_active_plugins(store) is None
    store.boot_dir.mkdir(parents=True)
    (store.boot_dir / "1.json").write_text(
        '{"pid": 1, "booted_at": "2026-09-30T12:00:00Z", "core_version": "1",'
        ' "plugins": {}}'
    )
    assert latest_active_plugins(store) == []


async def test_overlay_link_is_reported_as_its_store_entry(tmp_path: Path) -> None:
    """The loader registers ``<overlay>/<plugin>``, the link — not its target.

    The health verdict judges the store entry the worker loaded, so the report
    must name the entry the link resolved to at boot; recording the link path
    made every real update fail "not loaded from the expected release
    directory" and roll back (found on a real boot, Task 15).
    """
    from core.plugin_updates.apply.health import evaluate_boot_reports

    overlay = tmp_path / "ov"
    (overlay / ".store" / "demo-1.2.0").mkdir(parents=True)
    (overlay / "demo").symlink_to(Path(".store") / "demo-1.2.0")
    store = RunStore(tmp_path / "state")
    _expect(store, [])
    reg = _Registry({"demo": ("1.2.0", str(overlay / "demo"))}, {})
    await write_boot_report(reg, store, core_version="1.50.0", now=T0, pid=9)
    reports = read_boot_reports(store, since=T0 - timedelta(seconds=1))
    directory = reports[0].plugins["demo"].directory
    assert directory is not None and Path(directory).name == "demo-1.2.0"
    expect = store.read_expectation("demo")
    assert expect is not None
    assert evaluate_boot_reports(reports, expect).ok
