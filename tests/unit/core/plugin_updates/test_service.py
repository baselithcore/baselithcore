from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core._core_version import CORE_VERSION
from core.config.plugin_updates import PluginUpdateConfig
from core.plugin_updates import service as svc_mod
from core.plugin_updates.models import (
    CheckReport,
    ReleaseInfo,
    SystemUpdate,
    UpdateCandidate,
)


def _cfg(tmp: Path) -> PluginUpdateConfig:
    src = tmp / "m.yaml"
    src.write_text("mirrors:\n  demo:\n    repo: git@github.com:o/r.git\n")
    return PluginUpdateConfig(
        sources_file=src, cache_dir=tmp / "c", core_update_repo=""
    )


def _report() -> CheckReport:
    return CheckReport(
        checked_at=datetime.now(UTC),
        candidates=[
            UpdateCandidate(
                plugin="demo",
                installed_version="1.0.0",
                latest=None,
                available=True,
                trust="provenance",
            )
        ],
    )


async def test_check_failure_keeps_cached_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path), bundled_root=tmp_path)
    prev = _report()
    svc._cache.save(prev)

    async def boom(*a: object, **k: object) -> CheckReport:
        raise RuntimeError("network down")

    monkeypatch.setattr(svc_mod, "run_check", boom)
    report = await svc.check_now()
    # Served candidates carry install guidance; the cached ones never do.
    bare = [c.model_copy(update={"install": None}) for c in report.candidates]
    assert bare == prev.candidates and report.error == "RuntimeError"
    assert svc.report() is not None and svc.report().error == "RuntimeError"  # type: ignore[union-attr]


async def test_event_emitted_once_per_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    emitted: list[tuple[str, dict]] = []

    class _Bus:
        async def emit(self, name: str, data: dict | None = None, **_: object) -> int:
            emitted.append((name, data or {}))
            return 0

    monkeypatch.setattr(svc_mod, "get_event_bus", lambda: _Bus())
    report = _report()

    async def fake(*a: object, **k: object) -> CheckReport:
        return report

    monkeypatch.setattr(svc_mod, "run_check", fake)
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path), bundled_root=tmp_path)
    await svc.check_now()
    await svc.check_now()
    assert [n for n, _ in emitted] == ["plugin.update_available"]
    # A restart does not re-emit.
    again = svc_mod.PluginUpdateService(_cfg(tmp_path), bundled_root=tmp_path)
    await again.check_now()
    assert len(emitted) == 1


async def test_loop_survives_errors_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def flaky(*a: object, **k: object) -> CheckReport:
        nonlocal calls
        calls += 1
        raise RuntimeError("x")

    monkeypatch.setattr(svc_mod, "run_check", flaky)
    monkeypatch.setattr(svc_mod, "FIRST_RUN_DELAY_SECONDS", 0)
    cfg = _cfg(tmp_path)
    svc = svc_mod.PluginUpdateService(cfg, bundled_root=tmp_path)
    monkeypatch.setattr(svc, "_interval", 0.01)
    await svc.start()
    await asyncio.sleep(0.15)
    await svc.stop()
    assert calls >= 2
    assert svc._task is None


def test_singleton_accessors(tmp_path: Path) -> None:
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path), bundled_root=tmp_path)
    svc_mod.set_plugin_update_service(svc)
    assert svc_mod.get_plugin_update_service() is svc
    svc_mod.set_plugin_update_service(None)
    assert svc_mod.get_plugin_update_service() is None


async def test_error_is_sanitized_to_type_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path), bundled_root=tmp_path)

    async def boom(*a: object, **k: object) -> CheckReport:
        raise RuntimeError("Bearer ghp_SECRETTOKEN123 rejected")

    monkeypatch.setattr(svc_mod, "run_check", boom)
    report = await svc.check_now()
    assert report.error == "RuntimeError"
    assert "ghp_" not in (svc.report().error or "")  # type: ignore[union-attr]


async def test_source_error_message_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.plugin_updates.sources import SourceError

    svc = svc_mod.PluginUpdateService(_cfg(tmp_path), bundled_root=tmp_path)

    async def boom(*a: object, **k: object) -> CheckReport:
        raise SourceError("github returned 403")

    monkeypatch.setattr(svc_mod, "run_check", boom)
    report = await svc.check_now()
    assert report.error == "SourceError: github returned 403"


def _sys_update(version: str = "1.15.0", *, error: str | None = None) -> SystemUpdate:
    return SystemUpdate(
        repo="o/r",
        installed_version=CORE_VERSION,
        latest=ReleaseInfo(
            plugin="core", version=version, tag=f"v{version}", published_at=None
        ),
        available=error is None,
        behind=1,
        error=error,
    )


def _sys_cfg(tmp: Path, *, with_plugins: bool) -> PluginUpdateConfig:
    if with_plugins:
        cfg = _cfg(tmp)
        return cfg.model_copy(update={"core_update_repo": "o/r"})
    return PluginUpdateConfig(
        sources_file=None, cache_dir=tmp / "c", core_update_repo="o/r"
    )


async def test_system_only_skips_plugins_and_emits_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    emitted: list[tuple[str, dict]] = []

    class _Bus:
        async def emit(self, name: str, data: dict | None = None, **_: object) -> int:
            emitted.append((name, data or {}))
            return 0

    async def no_plugins(*a: object, **k: object) -> CheckReport:
        raise AssertionError("plugin check must not run")

    async def fake_system(*a: object, **k: object) -> SystemUpdate:
        return _sys_update()

    monkeypatch.setattr(svc_mod, "get_event_bus", lambda: _Bus())
    monkeypatch.setattr(svc_mod, "run_check", no_plugins)
    monkeypatch.setattr(svc_mod, "check_system", fake_system)
    svc = svc_mod.PluginUpdateService(_sys_cfg(tmp_path, with_plugins=False))
    report = await svc.check_now()
    await svc.check_now()
    assert (
        report.system is not None and report.candidates == [] and report.error is None
    )
    assert [n for n, _ in emitted] == ["system.update_available"]
    assert emitted[0][1]["latest_version"] == "1.15.0"
    assert "system:1.15.0" in svc._cache.load_notified()


async def test_system_failure_does_not_hide_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def plugins(*a: object, **k: object) -> CheckReport:
        return _report()

    async def boom(*a: object, **k: object) -> SystemUpdate:
        raise RuntimeError("Bearer ghp_SECRET")

    monkeypatch.setattr(svc_mod, "run_check", plugins)
    monkeypatch.setattr(svc_mod, "check_system", boom)
    svc = svc_mod.PluginUpdateService(_sys_cfg(tmp_path, with_plugins=True))
    report = await svc.check_now()
    assert len(report.candidates) == 1 and report.error is None
    assert report.system is not None and report.system.error == "RuntimeError"


async def test_plugin_failure_does_not_hide_system(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*a: object, **k: object) -> CheckReport:
        raise RuntimeError("x")

    async def fake_system(*a: object, **k: object) -> SystemUpdate:
        return _sys_update()

    monkeypatch.setattr(svc_mod, "run_check", boom)
    monkeypatch.setattr(svc_mod, "check_system", fake_system)
    svc = svc_mod.PluginUpdateService(_sys_cfg(tmp_path, with_plugins=True))
    report = await svc.check_now()
    assert report.error == "RuntimeError"
    assert report.system is not None and report.system.available


async def test_system_disabled_leaves_system_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def plugins(*a: object, **k: object) -> CheckReport:
        return _report()

    monkeypatch.setattr(svc_mod, "run_check", plugins)
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path), bundled_root=tmp_path)
    assert (await svc.check_now()).system is None


async def test_unexpected_system_exception_keeps_previous_security(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = _sys_update().model_copy(update={"security": True, "severity": "high"})
    svc = svc_mod.PluginUpdateService(_sys_cfg(tmp_path, with_plugins=False))
    svc._cache.save(
        CheckReport(checked_at=datetime.now(UTC), candidates=[], system=prior)
    )

    async def boom(*a: object, **k: object) -> SystemUpdate:
        raise RuntimeError("x")

    monkeypatch.setattr(svc_mod, "check_system", boom)
    report = await svc.check_now()
    assert report.system is not None
    assert report.system.security and report.system.severity == "high"
    assert report.system.error == "RuntimeError" and report.system.available


def _gauge_components() -> set[str]:
    from core.observability.metrics import UPDATE_AVAILABLE

    return {
        s.labels["component"]
        for m in UPDATE_AVAILABLE.collect()
        for s in m.samples
        if s.value
    }


async def test_start_publishes_the_cached_report_and_check_updates_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.plugin_updates.metrics import publish_update_metrics

    publish_update_metrics(None)
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path), bundled_root=tmp_path)
    svc._cache.save(_report())
    monkeypatch.setattr(svc_mod, "FIRST_RUN_DELAY_SECONDS", 3600)
    await svc.start()
    try:
        assert _gauge_components() == {"plugin:demo"}
    finally:
        await svc.stop()

    async def cleared(*a: object, **k: object) -> CheckReport:
        return CheckReport(checked_at=datetime.now(UTC), candidates=[])

    monkeypatch.setattr(svc_mod, "run_check", cleared)
    await svc.check_now()
    assert _gauge_components() == set()


async def test_cache_write_failure_still_publishes_and_announces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    emitted: list[str] = []
    published: list[CheckReport | None] = []

    class _Bus:
        async def emit(self, name: str, data: dict | None = None, **_: object) -> int:
            emitted.append(name)
            return 0

    async def fake(*a: object, **k: object) -> CheckReport:
        return _report()

    def unwritable(*a: object, **k: object) -> None:
        raise PermissionError("read-only file system")

    monkeypatch.setattr(svc_mod, "get_event_bus", lambda: _Bus())
    monkeypatch.setattr(svc_mod, "run_check", fake)
    monkeypatch.setattr(svc_mod, "publish_update_metrics", published.append)
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path), bundled_root=tmp_path)
    monkeypatch.setattr(svc._cache, "save", unwritable)
    monkeypatch.setattr(svc._cache, "save_notified", unwritable)
    with caplog.at_level("WARNING"):
        report = await svc.check_now()
    assert published == [report]
    assert emitted == ["plugin.update_available"]
    assert "plugin_update_cache_write_failed: PermissionError" in caplog.text


async def test_request_check_is_throttled_per_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def fake(*a: object, **k: object) -> CheckReport:
        nonlocal calls
        calls += 1
        return _report()

    clock = [1000.0]
    monkeypatch.setattr(svc_mod, "run_check", fake)
    monkeypatch.setattr(svc_mod.time, "monotonic", lambda: clock[0])
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path), bundled_root=tmp_path)
    first = await svc.request_check()
    clock[0] += svc_mod.CHECK_COOLDOWN_SECONDS - 1
    assert await svc.request_check() is first
    assert calls == 1
    # The periodic path is never throttled.
    await svc.check_now()
    assert calls == 2
    clock[0] += svc_mod.CHECK_COOLDOWN_SECONDS
    await svc.request_check()
    assert calls == 3
