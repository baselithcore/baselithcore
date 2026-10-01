"""A cache written before the signed file list must never serve an update."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core._core_version import CORE_VERSION
from core.config.plugin_updates import PluginUpdateConfig
from core.plugin_updates import service as svc_mod
from core.plugin_updates.cache import CACHE_FORMAT, UpdateCache
from core.plugin_updates.models import (
    CheckReport,
    ReleaseInfo,
    SystemUpdate,
    UpdateCandidate,
)

PUBLIC = "baselithcore/baselithcore"


def _legacy_available() -> dict[str, object]:
    """A last_check.json exactly as the pre-format checker saved it."""
    report = CheckReport(
        checked_at=datetime.now(UTC),
        candidates=[
            UpdateCandidate(
                plugin="demo",
                installed_version="1.1.0",
                latest=ReleaseInfo(
                    plugin="demo", version="1.2.0", tag="v1.2.0", published_at=None
                ),
                available=True,
            )
        ],
    )
    return json.loads(report.model_dump_json())


def _seed_old_cache(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "last_check.json").write_text(json.dumps(_legacy_available()))
    old = root / "tarballs" / "demo-1.2.0.tar.gz"
    old.parent.mkdir()
    old.write_bytes(b"verified by the old checker")
    return old


def test_report_without_format_keeps_no_candidates(tmp_path: Path) -> None:
    _seed_old_cache(tmp_path / "c")
    report = UpdateCache(tmp_path / "c").load()
    assert report is not None and report.candidates == [] and report.system is None


@pytest.mark.parametrize("fmt", [1, CACHE_FORMAT - 1, "2", True, None])
def test_report_of_an_older_format_drops_candidates(
    tmp_path: Path, fmt: object
) -> None:
    root = tmp_path / "c"
    root.mkdir()
    (root / "last_check.json").write_text(
        json.dumps({**_legacy_available(), "cache_format": fmt})
    )
    report = UpdateCache(root).load()
    assert report is not None and report.candidates == []


def _security_notice() -> SystemUpdate:
    return SystemUpdate(
        repo=PUBLIC,
        installed_version=CORE_VERSION,
        latest=ReleaseInfo(
            plugin="core", version="99.0.0", tag="v99.0.0", published_at=None
        ),
        available=True,
        behind=1,
        security=True,
        severity="critical",
    )


def _old_report_with_security(root: Path) -> dict[str, object]:
    root.mkdir(parents=True)
    old = {**_legacy_available(), "system": _security_notice().model_dump(mode="json")}
    (root / "last_check.json").write_text(json.dumps(old))
    return old


def test_older_format_keeps_the_system_notice(tmp_path: Path) -> None:
    old = _old_report_with_security(tmp_path / "c")
    report = UpdateCache(tmp_path / "c").load()
    assert report is not None and report.candidates == []
    assert report.system == _security_notice()
    assert report.checked_at == CheckReport.model_validate(old).checked_at


def test_older_format_with_a_broken_system_block_is_no_report(tmp_path: Path) -> None:
    root = tmp_path / "c"
    root.mkdir()
    (root / "last_check.json").write_text(
        json.dumps({**_legacy_available(), "system": {"repo": 1}})
    )
    assert UpdateCache(root).load() is None


async def test_failed_system_check_carries_the_old_security_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_report_with_security(tmp_path / "c")
    cfg = PluginUpdateConfig(sources_file=None, cache_dir=tmp_path / "c")
    svc = svc_mod.PluginUpdateService(cfg, bundled_root=tmp_path)
    served = svc.report()
    assert served is not None and served.system is not None
    assert served.system.security and served.candidates == []

    async def unreachable(*a: object, **k: object) -> SystemUpdate:
        raise RuntimeError("github down")

    monkeypatch.setattr(svc_mod, "check_system", unreachable)
    monkeypatch.setattr(svc_mod, "publish_update_metrics", lambda r: None)
    report = await svc.check_now()
    assert report.system is not None and report.system.error == "RuntimeError"
    assert report.system.security and report.system.severity == "critical"
    assert report.system.available and report.candidates == []


def test_save_load_round_trip_carries_the_format(tmp_path: Path) -> None:
    cache = UpdateCache(tmp_path / "c")
    report = CheckReport.model_validate(_legacy_available())
    cache.save(report)
    saved = json.loads((tmp_path / "c" / "last_check.json").read_text())
    assert saved["cache_format"] == CACHE_FORMAT
    assert cache.load() == report


def test_tarballs_live_in_a_versioned_dir(tmp_path: Path) -> None:
    path = UpdateCache(tmp_path / "c").tarball_path("demo", "1.2.0")
    assert path.parent.name == f"tarballs-v{CACHE_FORMAT}"


def test_purge_legacy_removes_the_old_tarballs(tmp_path: Path) -> None:
    old = _seed_old_cache(tmp_path / "c")
    cache = UpdateCache(tmp_path / "c")
    keep = cache.tarball_path("demo", "1.2.0")
    keep.parent.mkdir(parents=True)
    keep.write_bytes(b"verified under format 2")
    cache.purge_legacy()
    assert not old.parent.exists() and keep.exists()
    cache.purge_legacy()  # idempotent


async def test_start_serves_no_stale_update_and_drops_old_tarballs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.plugin_updates.metrics import publish_update_metrics

    src = tmp_path / "m.yaml"
    src.write_text("mirrors:\n  demo:\n    repo: git@github.com:o/r.git\n")
    cfg = PluginUpdateConfig(
        sources_file=src, cache_dir=tmp_path / "c", core_update_repo=""
    )
    old = _seed_old_cache(tmp_path / "c")
    published: list[CheckReport | None] = []
    monkeypatch.setattr(
        svc_mod, "publish_update_metrics", lambda r: published.append(r)
    )
    monkeypatch.setattr(svc_mod, "FIRST_RUN_DELAY_SECONDS", 3600)
    svc = svc_mod.PluginUpdateService(cfg, bundled_root=tmp_path)
    stale = svc.report()
    assert stale is not None and stale.candidates == []
    await svc.start()
    try:
        assert len(published) == 1 and published[0] is not None
        assert published[0].candidates == [] and not old.parent.exists()
    finally:
        await svc.stop()
        publish_update_metrics(None)
