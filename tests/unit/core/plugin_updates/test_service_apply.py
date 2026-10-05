from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core._core_version import CORE_VERSION
from core.config.plugin_update_apply import UpdateApplyConfig
from core.config.plugin_updates import PluginUpdateConfig
from core.plugin_updates import service as svc_mod
from core.plugin_updates.apply import eligibility
from core.plugin_updates.apply.models import RunKind, UpdaterHeartbeat
from core.plugin_updates.apply.store import RunStore
from core.plugin_updates.cache import UpdateCache
from core.plugin_updates.models import (
    CheckReport,
    ReleaseInfo,
    SignedAssets,
    UpdateCandidate,
)


def _service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    bundled_version: str,
    apply_enabled: bool,
    heartbeat: bool,
) -> svc_mod.PluginUpdateService:
    plugin = tmp_path / "plugins" / "demo"
    plugin.mkdir(parents=True)
    (plugin / "manifest.yaml").write_text(
        f"name: demo\nversion: {bundled_version}\n", encoding="utf-8"
    )
    cache = UpdateCache(tmp_path / "cache")
    cache.save(
        CheckReport(
            checked_at=datetime.now(UTC),
            candidates=[
                UpdateCandidate(
                    plugin="demo",
                    installed_version="1.1.0",
                    available=True,
                    trust="signed",
                    latest=ReleaseInfo(
                        plugin="demo", version="1.2.0", tag="v1.2.0", published_at=None
                    ),
                    signed_assets=SignedAssets(
                        verified=True, tarball_sha256="f" * 64, files_count=3
                    ),
                )
            ],
        )
    )
    store = RunStore(tmp_path / "state")
    if heartbeat:
        now = datetime.now(UTC)
        store.write_heartbeat(
            UpdaterHeartbeat(
                pid=1,
                started_at=now,
                at=now,
                core_version=CORE_VERSION,
                enabled=True,
                overlay_root=str(tmp_path / "ov"),
                overlay_writable=True,
                restart_configured=True,
            )
        )
    monkeypatch.setattr(svc_mod, "detect_install_method", lambda cfg: ("source", True))
    monkeypatch.setattr(eligibility, "_in_container", lambda root: False)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    return svc_mod.PluginUpdateService(
        PluginUpdateConfig(
            cache_dir=tmp_path / "cache", trust="signed", sources_file=None
        ),
        bundled_root=tmp_path / "plugins",
        apply_config=UpdateApplyConfig(enabled=apply_enabled),
        run_store=store,
    )


def test_served_candidate_carries_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _service(
        tmp_path,
        monkeypatch,
        bundled_version="1.1.0",
        apply_enabled=True,
        heartbeat=True,
    )
    report = svc.report()
    assert report is not None
    cand = report.candidates[0]
    assert cand.apply is not None and cand.apply.installable


def _svc(
    tmp_path: Path, mp: pytest.MonkeyPatch, **kw: object
) -> svc_mod.PluginUpdateService:
    opts: dict[str, object] = {
        "bundled_version": "1.1.0",
        "apply_enabled": True,
        "heartbeat": True,
    }
    return _service(tmp_path, mp, **(opts | kw))  # type: ignore[arg-type]


def _fake_check(
    svc: svc_mod.PluginUpdateService, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = svc._cache.load()
    assert saved is not None

    async def fake(*a: object, **k: object) -> CheckReport:
        return saved.model_copy(update={"checked_at": datetime.now(UTC)})

    monkeypatch.setattr(svc_mod, "run_check", fake)
    src = svc._config.cache_dir.parent / "sources.yaml"
    src.write_text("mirrors:\n  demo:\n    repo: git@github.com:o/r.git\n")
    svc._config = svc._config.model_copy(
        update={"sources_file": src, "core_update_repo": ""}
    )


async def test_apply_is_never_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _svc(tmp_path, monkeypatch)
    _fake_check(svc, monkeypatch)
    served = await svc.check_now()
    assert served.candidates[0].apply is not None
    saved = json.loads((tmp_path / "cache" / "last_check.json").read_text())
    assert saved["candidates"][0].get("apply") is None


def _start_run(tmp_path: Path) -> str:
    run = RunStore(tmp_path / "state").create(
        kind=RunKind.UPDATE,
        plugin="demo",
        from_version="1.1.0",
        to_version="1.2.0",
        tarball_sha256="f" * 64,
        requested_by="admin",
        approval_required=False,
        approval_ttl_seconds=60,
    )
    return run.id


def test_live_run_is_served_as_run_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _svc(tmp_path, monkeypatch)
    run_id = _start_run(tmp_path)
    report = svc.report()
    assert report is not None
    apply = report.candidates[0].apply
    assert apply is not None and not apply.installable
    assert apply.active_run == run_id
    assert "run_active" in [b.value for b in apply.blockers]


async def test_cooldown_hit_restamps_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _svc(tmp_path, monkeypatch)
    _fake_check(svc, monkeypatch)
    first = (await svc.request_check()).candidates[0].apply
    assert first is not None and first.installable
    run_id = _start_run(tmp_path)
    second = (await svc.request_check()).candidates[0].apply  # inside the cooldown
    assert second is not None and not second.installable
    assert second.active_run == run_id


def test_hyphenated_plugin_name_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _svc(tmp_path, monkeypatch)
    saved = svc._cache.load()
    assert saved is not None
    cand = saved.candidates[0].model_copy(update={"plugin": "my-plugin"})
    svc._cache.save(saved.model_copy(update={"candidates": [cand]}))
    report = svc.report()
    assert report is not None and report.candidates[0].apply is None


def test_unreadable_plugin_tree_does_not_fail_the_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _svc(tmp_path, monkeypatch)

    def boom(root: Path) -> dict[str, str]:
        raise OSError("denied")

    monkeypatch.setattr(svc_mod, "installed_versions", boom)
    report = svc.report()
    assert report is not None and report.candidates[0].apply is None


def test_reached_version_is_not_offered_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _service(
        tmp_path,
        monkeypatch,
        bundled_version="1.2.0",
        apply_enabled=True,
        heartbeat=True,
    )
    report = svc.report()
    assert report is not None
    cand = report.candidates[0]
    assert cand.available is False and cand.apply is None


def test_disabled_and_offline_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _service(
        tmp_path,
        monkeypatch,
        bundled_version="1.1.0",
        apply_enabled=False,
        heartbeat=False,
    )
    report = svc.report()
    assert report is not None
    apply = report.candidates[0].apply
    assert apply is not None and not apply.installable
    assert [b.value for b in apply.blockers[:2]] == [
        "apply_disabled",
        "updater_offline",
    ]


def test_unloadable_apply_settings_never_disable_the_update_checker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> UpdateApplyConfig:
        raise ValueError("UPDATE_APPLY_RESTART_COMMAND=[secret-token")

    monkeypatch.setattr(svc_mod, "get_update_apply_config", broken)
    cfg = PluginUpdateConfig(cache_dir=tmp_path / "cache")
    with caplog.at_level("WARNING"):
        svc = svc_mod.PluginUpdateService(cfg, tmp_path / "plugins")
    assert svc._apply_config.enabled is False  # the kill switch stays off
    assert "ValueError" in caplog.text and "secret-token" not in caplog.text
