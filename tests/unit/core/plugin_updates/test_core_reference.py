"""The system notice always references the public core release and repo."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core._core_version import CORE_VERSION
from core.config import plugin_updates as cfg_mod
from core.config.plugin_updates import PluginUpdateConfig
from core.plugin_updates import service as svc_mod
from core.plugin_updates.models import CheckReport, ReleaseInfo, SystemUpdate

PUBLIC = "baselithcore/baselithcore"


def _cfg(tmp: Path, **extra: object) -> PluginUpdateConfig:
    return PluginUpdateConfig(sources_file=None, cache_dir=tmp / "c", **extra)


def _saved(svc: svc_mod.PluginUpdateService, system: SystemUpdate) -> None:
    svc._cache.save(
        CheckReport(checked_at=datetime.now(UTC), candidates=[], system=system)
    )


# --- configuration ---------------------------------------------------------


def test_core_repo_defaults_to_the_public_core() -> None:
    assert PluginUpdateConfig().core_update_repo == PUBLIC


def test_core_repo_env_and_empty_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORE_UPDATE_REPO", "fork/core")
    assert PluginUpdateConfig().core_update_repo == "fork/core"
    monkeypatch.setenv("CORE_UPDATE_REPO", "")
    cfg = PluginUpdateConfig(sources_file=None)
    assert not cfg.system_checks_enabled and not cfg.enabled


def test_legacy_system_update_repo_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYSTEM_UPDATE_REPO", "baselithcore/private-distribution")
    monkeypatch.delenv("CORE_UPDATE_REPO", raising=False)
    assert PluginUpdateConfig().core_update_repo == PUBLIC
    monkeypatch.setenv("SYSTEM_UPDATE_REPO", "")
    assert PluginUpdateConfig().core_update_repo == PUBLIC


def test_legacy_env_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(cfg_mod, "_legacy_warned", False)
    env = {"SYSTEM_UPDATE_REPO": "o/private"}
    with caplog.at_level(logging.WARNING, logger=cfg_mod.__name__):
        assert cfg_mod.warn_ignored_legacy_env(env)
        assert not cfg_mod.warn_ignored_legacy_env(env)
    warnings = [r for r in caplog.records if "SYSTEM_UPDATE_REPO" in r.getMessage()]
    assert len(warnings) == 1
    assert "ignored" in warnings[0].getMessage()


def test_no_warning_without_legacy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg_mod, "_legacy_warned", False)
    assert not cfg_mod.warn_ignored_legacy_env({"SYSTEM_UPDATE_REPO": ""})
    assert not cfg_mod.warn_ignored_legacy_env({})


def test_get_config_runs_the_legacy_check(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []
    monkeypatch.setattr(cfg_mod, "warn_ignored_legacy_env", lambda: seen.append(True))
    cfg_mod.get_plugin_update_config.cache_clear()
    try:
        cfg_mod.get_plugin_update_config()
    finally:
        cfg_mod.get_plugin_update_config.cache_clear()
    assert seen == [True]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://ops.example.com/upgrade", "https://ops.example.com/upgrade"),
        ("  https://ops.example.com/u  ", "https://ops.example.com/u"),
        ("", None),
        ("http://ops.example.com/upgrade", None),
        ("javascript:alert(1)", None),
        ("https:///no-host", None),
        ("https://user:pw@ops.example.com/", None),
    ],
)
def test_upgrade_guide_url_is_https_only(raw: str, expected: str | None) -> None:
    assert PluginUpdateConfig(upgrade_guide_url=raw).upgrade_guide_url == expected


def test_upgrade_guide_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYSTEM_UPGRADE_GUIDE_URL", "https://ops.example.com/x")
    assert PluginUpdateConfig().upgrade_guide_url == "https://ops.example.com/x"


# --- service ---------------------------------------------------------------


async def test_system_check_uses_core_version_and_core_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake(installed: str, slug: str, **_: object) -> SystemUpdate:
        calls.append((installed, slug))
        return SystemUpdate(repo=slug, installed_version=installed)

    monkeypatch.setattr(svc_mod, "check_system", fake)
    svc = svc_mod.PluginUpdateService(
        _cfg(tmp_path, upgrade_guide_url="https://ops.example.com/up")
    )
    report = await svc.check_now()
    assert calls == [(CORE_VERSION, PUBLIC)]
    assert report.system is not None
    assert report.system.component == "core"
    assert report.system.upgrade_guide_url == "https://ops.example.com/up"


def test_report_drops_a_notice_for_another_repo_or_version(tmp_path: Path) -> None:
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path))
    # A cache written before the notice referenced the public core: another
    # repo and the distribution's own version. It must not be served.
    _saved(
        svc,
        SystemUpdate(
            repo="baselithcore/private-distribution",
            installed_version="99.0.0",
            available=True,
            behind=1,
        ),
    )
    report = svc.report()
    assert report is not None and report.system is None
    _saved(svc, SystemUpdate(repo=PUBLIC, installed_version="0.0.1"))
    report = svc.report()
    assert report is not None and report.system is None


def test_report_serves_current_notice_with_current_guide(tmp_path: Path) -> None:
    svc = svc_mod.PluginUpdateService(
        _cfg(tmp_path, upgrade_guide_url="https://ops.example.com/new")
    )
    _saved(
        svc,
        SystemUpdate(
            repo=PUBLIC,
            installed_version=CORE_VERSION,
            upgrade_guide_url="https://ops.example.com/old",
            latest=ReleaseInfo(
                plugin="core", version="99.0.0", tag="v99.0.0", published_at=None
            ),
            available=True,
            behind=1,
        ),
    )
    report = svc.report()
    assert report is not None and report.system is not None
    assert report.system.available
    assert report.system.upgrade_guide_url == "https://ops.example.com/new"


def test_report_hides_the_notice_when_the_check_is_disabled(tmp_path: Path) -> None:
    on = svc_mod.PluginUpdateService(_cfg(tmp_path))
    _saved(on, SystemUpdate(repo=PUBLIC, installed_version=CORE_VERSION))
    off = svc_mod.PluginUpdateService(_cfg(tmp_path, core_update_repo=""))
    report = off.report()
    assert report is not None and report.system is None


def test_old_cache_json_still_loads(tmp_path: Path) -> None:
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path))
    legacy = {
        "checked_at": "2026-09-29T10:00:00Z",
        "candidates": [],
        "error": None,
        "system": {
            "component": "core",
            "repo": PUBLIC,
            "installed_version": CORE_VERSION,
            "available": False,
            "behind": 0,
            "major": False,
            "security": False,
            "severity": None,
            "advisories": [],
            "error": None,
        },
    }
    root = tmp_path / "c"
    root.mkdir()
    (root / "last_check.json").write_text(json.dumps(legacy), encoding="utf-8")
    report = svc.report()
    assert report is not None and report.system is not None
    assert report.system.upgrade_guide_url is None
