from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.config.plugin_update_apply import UpdateApplyConfig
from core.plugin_updates.apply.eligibility import apply_status
from core.plugin_updates.apply.models import ApplyRun, RunKind, UpdaterHeartbeat
from core.plugin_updates.apply.store import RunStore
from core.plugin_updates.models import (
    ApplyBlocker,
    Refusal,
    ReleaseInfo,
    SignedAssets,
    UpdateCandidate,
)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _cand(**assets: object) -> UpdateCandidate:
    signed = SignedAssets(
        **({"verified": True, "tarball_sha256": "f" * 64, "files_count": 3} | assets)  # type: ignore[arg-type]
    )
    return UpdateCandidate(
        plugin="demo",
        installed_version="1.1.0",
        available=True,
        signed_assets=signed,
        latest=ReleaseInfo(
            plugin="demo", version="1.2.0", tag="v1.2.0", published_at=None
        ),
    )


def _hb(**kw: object) -> UpdaterHeartbeat:
    base: dict[str, object] = {
        "pid": 1,
        "started_at": NOW,
        "at": NOW,
        "core_version": "1.50.0",
        "enabled": True,
        "overlay_root": "/ov",
        "overlay_writable": True,
        "restart_configured": True,
    }
    return UpdaterHeartbeat(**(base | kw))  # type: ignore[arg-type]


def _status(
    cand: UpdateCandidate,
    *,
    method: str = "source",
    hb: UpdaterHeartbeat | None = None,
    env: dict[str, str] | None = None,
    cfg: UpdateApplyConfig | None = None,
    root: Path | None = None,
    active: ApplyRun | None = None,
):  # type: ignore[no-untyped-def]
    return apply_status(
        cand,
        config=cfg or UpdateApplyConfig(enabled=True),
        method=method,  # type: ignore[arg-type]
        heartbeat=_hb() if hb is None else hb,
        active_run=active,
        core_version="1.50.0",
        now=NOW,
        environ=env or {},
        root=root or Path("/nonexistent-root"),
    )


def test_signed_host_install_is_installable() -> None:
    status = _status(_cand())
    assert status.installable and status.blockers == []


def test_kill_switch_first() -> None:
    status = _status(_cand(), cfg=UpdateApplyConfig(enabled=False))
    assert not status.installable
    assert status.blockers[0] is ApplyBlocker.APPLY_DISABLED


@pytest.mark.parametrize("method", ["helm", "docker", "custom"])
def test_non_host_methods_are_blocked(method: str) -> None:
    assert ApplyBlocker.NOT_HOST_INSTALL in _status(_cand(), method=method).blockers


def test_kubernetes_env_blocks_even_when_method_forced_to_source() -> None:
    status = _status(_cand(), env={"KUBERNETES_SERVICE_HOST": "10.0.0.1"})
    assert ApplyBlocker.NOT_HOST_INSTALL in status.blockers


def test_container_marker_blocks(tmp_path: Path) -> None:
    (tmp_path / ".dockerenv").write_text("")
    assert ApplyBlocker.NOT_HOST_INSTALL in _status(_cand(), root=tmp_path).blockers


def test_stale_heartbeat_is_offline() -> None:
    stale = _hb(at=NOW - timedelta(seconds=16))  # > 3 x 5 s
    assert ApplyBlocker.UPDATER_OFFLINE in _status(_cand(), hb=stale).blockers


def test_missing_heartbeat_is_offline() -> None:
    status = apply_status(
        _cand(),
        config=UpdateApplyConfig(enabled=True),
        method="source",
        heartbeat=None,
        active_run=None,
        core_version="1.50.0",
        now=NOW,
        environ={},
        root=Path("/nonexistent-root"),
    )
    assert ApplyBlocker.UPDATER_OFFLINE in status.blockers


def test_updater_on_another_core_version() -> None:
    hb = _hb(core_version="1.49.0")
    assert ApplyBlocker.UPDATER_MISMATCH in _status(_cand(), hb=hb).blockers


def test_overlay_not_writable() -> None:
    hb = _hb(overlay_writable=False)
    assert ApplyBlocker.OVERLAY_UNCONFIGURED in _status(_cand(), hb=hb).blockers


def test_provenance_only_release_is_unsigned() -> None:
    cand = _cand(verified=False, refusal=Refusal.ARTIFACT_MISSING)
    assert ApplyBlocker.UNSIGNED_RELEASE in _status(cand).blockers


def test_failed_signature_names_the_refusal() -> None:
    cand = _cand(verified=False, refusal=Refusal.FILES_MISMATCH, detail="extra: x")
    status = _status(cand)
    assert ApplyBlocker.SIGNATURE_FAILED in status.blockers
    assert "files_mismatch" in status.detail


def test_unsatisfied_dependency_blocks() -> None:
    cand = _cand(
        verified=False, refusal=Refusal.NEEDS_ENVIRONMENT_UPDATE, detail="httpx>=9"
    )
    assert _status(cand).blockers == [ApplyBlocker.NEEDS_ENVIRONMENT_UPDATE]


def test_host_build_required_blocks() -> None:
    cand = _cand(host_build_required=True)
    assert ApplyBlocker.HOST_BUILD_REQUIRED in _status(cand).blockers


def test_not_available_has_no_blockers_and_is_not_installable() -> None:
    cand = _cand().model_copy(update={"available": False})
    status = _status(cand)
    assert not status.installable and status.blockers == []


def _live_run(tmp_path: Path) -> ApplyRun:
    return RunStore(tmp_path / "state").create(
        kind=RunKind.UPDATE,
        plugin="demo",
        from_version="1.1.0",
        to_version="1.2.0",
        tarball_sha256="f" * 64,
        requested_by="admin",
        approval_required=False,
        approval_ttl_seconds=60,
    )


def test_live_run_blocks_and_is_named(tmp_path: Path) -> None:
    run = _live_run(tmp_path)
    status = _status(_cand(), active=run)
    assert not status.installable
    assert ApplyBlocker.RUN_ACTIVE in status.blockers
    assert status.active_run == run.id


def test_updater_reporting_disabled_is_apply_disabled() -> None:
    status = _status(_cand(), hb=_hb(enabled=False))
    assert status.blockers == [ApplyBlocker.APPLY_DISABLED]
    assert "disabled" in status.detail


def test_updater_without_restart_command_is_apply_disabled() -> None:
    status = _status(_cand(), hb=_hb(restart_configured=False))
    assert status.blockers == [ApplyBlocker.APPLY_DISABLED]
    assert "restart" in status.detail


def test_disabled_reasons_do_not_duplicate_the_blocker() -> None:
    status = _status(
        _cand(),
        cfg=UpdateApplyConfig(enabled=False),
        hb=_hb(enabled=False, restart_configured=False),
    )
    assert status.blockers == [ApplyBlocker.APPLY_DISABLED]


def test_every_reachable_blocker_in_console_order(tmp_path: Path) -> None:
    status = _status(
        _cand(host_build_required=True),
        cfg=UpdateApplyConfig(enabled=False),
        method="helm",
        hb=_hb(overlay_writable=False, core_version="1.49.0"),
        active=_live_run(tmp_path),
    )
    assert status.blockers == [
        ApplyBlocker.APPLY_DISABLED,
        ApplyBlocker.NOT_HOST_INSTALL,
        ApplyBlocker.OVERLAY_UNCONFIGURED,
        ApplyBlocker.UPDATER_MISMATCH,
        ApplyBlocker.HOST_BUILD_REQUIRED,
        ApplyBlocker.RUN_ACTIVE,
    ]


def test_offline_updater_and_bad_signature_order() -> None:
    cand = _cand(verified=False, refusal=Refusal.FILES_MISMATCH)
    status = _status(cand, hb=_hb(at=NOW - timedelta(minutes=5)))
    assert status.blockers == [
        ApplyBlocker.UPDATER_OFFLINE,
        ApplyBlocker.SIGNATURE_FAILED,
    ]


def test_candidate_without_signed_assets_is_unsigned() -> None:
    cand = _cand().model_copy(update={"signed_assets": None})
    assert _status(cand).blockers == [ApplyBlocker.UNSIGNED_RELEASE]


@pytest.mark.parametrize("refusal", [Refusal.LEGACY_RELEASE, None])
def test_unverified_legacy_or_unexplained_is_unsigned(refusal: Refusal | None) -> None:
    cand = _cand(verified=False, refusal=refusal)
    assert _status(cand).blockers == [ApplyBlocker.UNSIGNED_RELEASE]
