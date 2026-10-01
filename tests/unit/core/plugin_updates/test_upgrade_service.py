"""The upgrade instructions as the service serves them (and never caches)."""

from __future__ import annotations

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
from core.plugin_updates.system import check_system

from .test_system import _rel, _source


def _cfg(tmp: Path, **kw: object) -> PluginUpdateConfig:
    return PluginUpdateConfig(
        sources_file=None,
        cache_dir=tmp / "c",
        core_update_repo="o/r",
        install_method="pip",
        **kw,  # type: ignore[arg-type]
    )


def _release(version: str) -> ReleaseInfo:
    return ReleaseInfo(
        plugin="core",
        version=version,
        tag=f"v{version}",
        published_at=None,
        html_url=f"https://github.com/o/r/releases/tag/v{version}",
    )


def _report(*, available: bool = True) -> CheckReport:
    return CheckReport(
        checked_at=datetime.now(UTC),
        candidates=[
            UpdateCandidate(
                plugin="demo",
                installed_version="1.0.0",
                latest=_release("1.1.0"),
                available=True,
                trust="provenance",
            ),
            UpdateCandidate(
                plugin="other",
                installed_version="1.0.0",
                latest=None,
                available=False,
                trust="provenance",
            ),
        ],
        system=SystemUpdate(
            repo="o/r",
            installed_version=CORE_VERSION,
            latest=_release("99.0.0") if available else None,
            available=available,
            behind=1 if available else 0,
        ),
    )


def test_served_report_carries_instructions_and_plugin_guidance(tmp_path: Path) -> None:
    svc = svc_mod.PluginUpdateService(
        _cfg(tmp_path, upgrade_guide_url="https://ops.example.com/u"),
        bundled_root=tmp_path,
    )
    svc._cache.save(_report())
    served = svc.report()
    assert served is not None and served.system is not None
    upgrade = served.system.upgrade
    assert upgrade is not None
    assert upgrade.method == "pip" and upgrade.detected is False
    assert upgrade.target_version == "99.0.0"
    assert upgrade.guide_url == "https://ops.example.com/u"
    demo, other = served.candidates
    assert demo.install is not None and demo.install.automated is False
    assert demo.install.guide_url == "https://ops.example.com/u"
    assert other.install is None


def test_no_instructions_when_the_core_is_current(tmp_path: Path) -> None:
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path), bundled_root=tmp_path)
    svc._cache.save(_report(available=False))
    served = svc.report()
    assert served is not None and served.system is not None
    assert served.system.upgrade is None


async def test_instructions_are_never_written_to_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_system(*a: object, **k: object) -> SystemUpdate:
        report = _report()
        assert report.system is not None
        return report.system

    monkeypatch.setattr(svc_mod, "check_system", fake_system)
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path), bundled_root=tmp_path)
    returned = await svc.check_now()
    assert returned.system is not None and returned.system.upgrade is not None
    saved = svc._cache.load()
    assert saved is not None and saved.system is not None
    assert saved.system.upgrade is None


def test_a_cached_upgrade_block_is_recomputed_on_read(tmp_path: Path) -> None:
    helm = svc_mod.PluginUpdateService(
        _cfg(tmp_path).model_copy(update={"install_method": "helm"}),
        bundled_root=tmp_path,
    )
    helm._cache.save(_report())
    stale = helm.report()
    assert stale is not None
    helm._cache.save(stale)  # an upgrade block written by an older build
    pip = svc_mod.PluginUpdateService(_cfg(tmp_path), bundled_root=tmp_path)
    served = pip.report()
    assert served is not None and served.system is not None
    assert served.system.upgrade is not None and served.system.upgrade.method == "pip"


async def test_check_system_records_the_upgrade_path() -> None:
    releases = [_rel(t) for t in ("v2.1.0", "v2.0.0", "v1.9.0", "v1.2.0")]
    result = await check_system("1.2.0", "o/r", source=_source(releases=releases))
    assert result.upgrade_path == ["1.9.0", "2.1.0"]


async def test_a_failed_release_lookup_keeps_the_known_path() -> None:
    previous = SystemUpdate(
        repo="o/r",
        installed_version="1.2.0",
        latest=_release("2.1.0"),
        available=True,
        behind=2,
        major=True,
        upgrade_path=["1.9.0", "2.1.0"],
    )
    result = await check_system(
        "1.2.0", "o/r", source=_source(rel_status=500), previous=previous
    )
    assert result.upgrade_path == ["1.9.0", "2.1.0"]


def test_an_old_cache_without_the_new_fields_still_loads(tmp_path: Path) -> None:
    raw = _report().model_dump(mode="json")
    assert raw["system"] is not None
    for key in ("upgrade_path", "upgrade"):
        raw["system"].pop(key)
    for cand in raw["candidates"]:
        cand.pop("install")
    loaded = CheckReport.model_validate(raw)
    assert loaded.system is not None and loaded.system.upgrade_path == []
