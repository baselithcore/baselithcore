"""A downstream distribution's own releases are announced beside the core's."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from baselith.core import _version
from baselith.core._core_version import CORE_VERSION
from baselith.core._version import __version__ as FRAMEWORK_VERSION
from baselith.core.config.plugin_updates import PluginUpdateConfig
from baselith.core.plugin_updates import distribution as dist_mod
from baselith.core.plugin_updates import service as svc_mod
from baselith.core.plugin_updates._system_notice import notice_announcement
from baselith.core.plugin_updates.cache import UpdateCache
from baselith.core.plugin_updates.metrics import publish_update_metrics
from baselith.core.plugin_updates.models import CheckReport, ReleaseInfo, SystemUpdate

from .test_metrics import _active
from .test_system import _rel, _source

DIST = "o/dist"
PUBLIC = "baselithcore/baselithcore"


@pytest.fixture(autouse=True)
def _a_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test runs as a distribution unless it removes the marker."""
    monkeypatch.setattr(_version, "__distribution__", "acme-dist", raising=False)


def _cfg(tmp: Path, **extra: object) -> PluginUpdateConfig:
    base: dict[str, object] = {"sources_file": None, "cache_dir": tmp / "c"}
    base.update(extra)
    return PluginUpdateConfig(**base)  # type: ignore[arg-type]


def _notice(
    *,
    component: str = "distribution",
    repo: str = DIST,
    installed: str = FRAMEWORK_VERSION,
    latest: str | None = "99.0.0",
) -> SystemUpdate:
    release = (
        ReleaseInfo(plugin="core", version=latest, tag=f"v{latest}", published_at=None)
        if latest
        else None
    )
    return SystemUpdate(
        component=component,
        repo=repo,
        installed_version=installed,
        latest=release,
        available=latest is not None,
        behind=1 if latest else 0,
    )


class _Bus:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, object]]] = []

    async def emit(self, name: str, data: dict | None = None, **_: object) -> int:
        self.emitted.append((name, data or {}))
        return 1


def _fake_checks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    core_error: Exception | None = None,
    dist_error: Exception | None = None,
) -> list[tuple[str, str, str]]:
    calls: list[tuple[str, str, str]] = []

    def fake(which: str, error: Exception | None):
        async def check(installed: str, slug: str, **_: object) -> SystemUpdate:
            calls.append((which, installed, slug))
            if error is not None:
                raise error
            return _notice(component="core", repo=slug, installed=installed)

        return check

    monkeypatch.setattr(svc_mod, "check_system", fake("core", core_error))
    monkeypatch.setattr(dist_mod, "check_system", fake("dist", dist_error))
    return calls


# --- configuration ---------------------------------------------------------


def test_distribution_checks_off_when_repo_empty(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, core_update_repo="")
    assert cfg.distribution_update_repo == ""
    assert not cfg.distribution_checks_enabled and not cfg.enabled
    on = _cfg(tmp_path, core_update_repo="", distribution_update_repo=DIST)
    assert on.distribution_checks_enabled and on.enabled
    assert not on.system_checks_enabled  # the core check stays core-only


def test_distribution_repo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISTRIBUTION_UPDATE_REPO", "acme/dist")
    assert PluginUpdateConfig().distribution_update_repo == "acme/dist"


async def test_distribution_checks_off_skip_the_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_checks(monkeypatch)
    report = await svc_mod.PluginUpdateService(_cfg(tmp_path)).check_now()
    assert [c[0] for c in calls] == ["core"]
    assert report.distribution is None


# --- the check -------------------------------------------------------------


async def test_distribution_notice_compares_version_not_core_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_checks(monkeypatch)
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path, distribution_update_repo=DIST))
    report = await svc.check_now()
    assert ("dist", FRAMEWORK_VERSION, DIST) in calls
    assert ("core", CORE_VERSION, PUBLIC) in calls
    assert report.distribution is not None
    assert report.distribution.component == "distribution"
    assert report.distribution.installed_version == FRAMEWORK_VERSION
    assert report.system is not None and report.system.component == "core"


async def test_check_distribution_reads_the_distribution_releases() -> None:
    source = _source([_rel("v1.14.0"), _rel("v1.15.0")])
    notice = await dist_mod.check_distribution(
        "1.14.0", DIST, source=source, previous=None
    )
    assert notice.component == "distribution" and notice.repo == DIST
    assert notice.available and notice.latest is not None
    assert notice.latest.version == "1.15.0"


async def test_public_core_has_no_distribution_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(_version, "__distribution__")
    assert dist_mod.distribution_name() is None
    calls = _fake_checks(monkeypatch)
    cfg = _cfg(tmp_path, distribution_update_repo=DIST)
    assert not cfg.distribution_checks_enabled
    report = await svc_mod.PluginUpdateService(cfg).check_now()
    assert [c[0] for c in calls] == ["core"]  # the core notice is not duplicated
    assert report.distribution is None and report.system is not None


async def test_distribution_failure_does_not_break_core_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_checks(monkeypatch, dist_error=RuntimeError("boom"))
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path, distribution_update_repo=DIST))
    report = await svc.check_now()
    assert report.system is not None and report.system.error is None
    assert report.distribution is not None
    assert report.distribution.error == "RuntimeError"
    assert report.distribution.component == "distribution"

    _fake_checks(monkeypatch, core_error=RuntimeError("down"))
    svc._last = None
    report = await svc.check_now()
    assert report.system is not None and report.system.error == "RuntimeError"
    assert report.distribution is not None and report.distribution.error is None


async def test_failed_distribution_check_keeps_the_last_known_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_checks(monkeypatch)
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path, distribution_update_repo=DIST))
    await svc.check_now()
    _fake_checks(monkeypatch, dist_error=RuntimeError("boom"))
    report = await svc.check_now()
    assert report.distribution is not None and report.distribution.available
    assert report.distribution.error == "RuntimeError"


# --- cache and scoping -----------------------------------------------------


def test_old_cache_without_distribution_loads(tmp_path: Path) -> None:
    root = tmp_path / "c"
    root.mkdir()
    body = {
        "cache_format": 2,
        "checked_at": "2026-11-13T10:00:00Z",
        "candidates": [],
        "error": None,
        "system": None,
    }
    (root / "last_check.json").write_text(json.dumps(body), encoding="utf-8")
    loaded = UpdateCache(root).load()
    assert loaded is not None and loaded.distribution is None
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path, distribution_update_repo=DIST))
    served = svc.report()
    assert served is not None and served.distribution is None


def test_other_format_cache_keeps_distribution(tmp_path: Path) -> None:
    root = tmp_path / "c"
    root.mkdir()
    body = {
        "cache_format": 1,
        "checked_at": "2026-11-13T10:00:00Z",
        "candidates": [],
        "distribution": _notice().model_dump(mode="json"),
    }
    (root / "last_check.json").write_text(json.dumps(body), encoding="utf-8")
    loaded = UpdateCache(root).load()
    assert loaded is not None and loaded.distribution == _notice()


def _saved(tmp_path: Path, notice: SystemUpdate) -> None:
    UpdateCache(tmp_path / "c").save(
        CheckReport(checked_at=datetime.now(UTC), candidates=[], distribution=notice)
    )


@pytest.mark.parametrize(
    "notice",
    [
        _notice(repo="other/dist"),
        _notice(installed="0.0.1"),
        _notice(component="core"),
    ],
)
def test_distribution_scoped_to_repo_and_version(
    tmp_path: Path, notice: SystemUpdate
) -> None:
    _saved(tmp_path, notice)
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path, distribution_update_repo=DIST))
    served = svc.report()
    assert served is not None and served.distribution is None


def test_current_distribution_notice_is_served(tmp_path: Path) -> None:
    _saved(tmp_path, _notice())
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path, distribution_update_repo=DIST))
    served = svc.report()
    assert served is not None and served.distribution == _notice()


def test_distribution_notice_hidden_once_checks_are_off(tmp_path: Path) -> None:
    _saved(tmp_path, _notice())
    served = svc_mod.PluginUpdateService(_cfg(tmp_path)).report()
    assert served is not None and served.distribution is None


def test_distribution_notice_gets_no_core_upgrade_guide(tmp_path: Path) -> None:
    guide = "https://ops.example.com/up"
    _saved(
        tmp_path,
        _notice().model_copy(update={"upgrade_guide_url": "https://old.example.com"}),
    )
    svc = svc_mod.PluginUpdateService(
        _cfg(tmp_path, distribution_update_repo=DIST, upgrade_guide_url=guide)
    )
    served = svc.report()
    assert served is not None and served.distribution is not None
    assert served.distribution.upgrade_guide_url is None
    assert served.distribution.upgrade is None


# --- announcement and metrics ----------------------------------------------


def test_distribution_event_key_is_distinct() -> None:
    core = notice_announcement(
        _notice(component="core", latest="1.4.0"), notified_key_prefix="system"
    )
    dist = notice_announcement(
        _notice(latest="1.4.0"), notified_key_prefix="distribution"
    )
    assert core is not None and dist is not None
    assert core[0] == "system:1.4.0" and dist[0] == "distribution:1.4.0"
    assert dist[1]["component"] == "distribution"


async def test_both_notices_announced_for_one_version_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_checks(monkeypatch)
    bus = _Bus()
    monkeypatch.setattr(svc_mod, "get_event_bus", lambda: bus)
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path, distribution_update_repo=DIST))
    await svc.check_now()
    components = sorted(str(d["component"]) for _, d in bus.emitted)
    assert components == ["core", "distribution"]
    assert {n for n, _ in bus.emitted} == {"system.update_available"}
    notified = UpdateCache(tmp_path / "c").load_notified()
    assert {"system:99.0.0", "distribution:99.0.0"} <= notified
    svc._last = None
    await svc.check_now()
    assert len(bus.emitted) == 2  # once per key


def test_metrics_publish_the_distribution(tmp_path: Path) -> None:
    report = CheckReport(
        checked_at=datetime.now(UTC), candidates=[], distribution=_notice()
    )
    publish_update_metrics(report)
    assert _active().get(("distribution", "false")) == 1
    publish_update_metrics(
        report.model_copy(update={"distribution": _notice(latest=None)})
    )
    assert ("distribution", "false") not in _active()
