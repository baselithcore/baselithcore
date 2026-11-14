"""Pre-flight: a list of actionable items, built from injected inputs only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from baselith.core.config.plugin_update_apply import UpdateApplyConfig
from baselith.core.plugin_updates.apply.drivers._core_guards import (
    CORE_ONLY_BLOCKERS,
)
from baselith.core.plugin_updates.apply.eligibility import apply_status
from baselith.core.plugin_updates.apply.models import (
    ApplyRun,
    RunKind,
    RunState,
    UpdaterHeartbeat,
)
from baselith.core.plugin_updates.models import (
    ApplyBlocker,
    CheckReport,
    ReleaseInfo,
    SignedAssets,
    SystemUpdate,
    UpdateCandidate,
)
from baselith.core.plugin_updates.preflight import (
    PreflightItem,
    PreflightLevel,
    build_preflight,
    normalize_subject,
)
from baselith.core.plugin_updates.upgrade_models import PluginBoundsIssue

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
CORE = "1.3.1"
GB = 1024**3


def _release(version: str, plugin: str = "core") -> ReleaseInfo:
    return ReleaseInfo(
        plugin=plugin, version=version, tag=f"v{version}", published_at=None
    )


def _plugin(
    name: str = "aura", *, installed: str = "1.0.0", latest: str = "1.1.0"
) -> UpdateCandidate:
    return UpdateCandidate(
        plugin=name,
        installed_version=installed,
        latest=_release(latest, name),
        available=True,
        signed_assets=SignedAssets(verified=True, tarball_sha256="a" * 64),
    )


def _notice(
    *, installed: str = "1.3.1", latest: str = "1.4.0", **extra: Any
) -> SystemUpdate:
    return SystemUpdate(
        repo="o/core",
        installed_version=installed,
        latest=_release(latest),
        available=True,
        behind=1,
        **extra,
    )


def _report(**extra: Any) -> CheckReport:
    base: dict[str, Any] = {"checked_at": NOW, "candidates": [_plugin()]}
    base.update(extra)
    return CheckReport(**base)


def _heartbeat(**extra: Any) -> UpdaterHeartbeat:
    base: dict[str, Any] = {
        "pid": 1,
        "started_at": NOW - timedelta(minutes=5),
        "at": NOW,
        "core_version": CORE,
        "enabled": True,
        "overlay_root": "/srv/overlay",
        "overlay_writable": True,
        "restart_configured": True,
    }
    base.update(extra)
    return UpdaterHeartbeat(**base)


CFG = UpdateApplyConfig(
    enabled=True, restart_command=["true"], state_dir=Path("/tmp/x-state")
)


def _run(subject: str, version: str | None = None, **over: Any) -> list[PreflightItem]:
    args: dict[str, Any] = {
        "report": _report(),
        "config": CFG,
        "heartbeat": _heartbeat(),
        "active_run": None,
        "bounds": [],
        "queue_probe": lambda: 0,
        "disk_free": lambda _p: 10 * GB,
        "now": NOW,
        "method": "source",
        "core_version": CORE,
        "distribution": False,
    }
    args.update(over)
    return build_preflight(subject, version, **args)


def _codes(items: list[PreflightItem]) -> list[str]:
    return [i.code for i in items]


@pytest.fixture(autouse=True)
def _host_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """The machine running the tests may be a container: pretend a host."""
    monkeypatch.setattr(
        "baselith.core.plugin_updates.apply.eligibility.host_install_ok",
        lambda method, **_: method in ("source", "pip", "custom"),
    )


def test_normalize_subject() -> None:
    assert normalize_subject("core") == "core"
    assert normalize_subject("aura") == "plugin:aura"
    assert normalize_subject("plugin:aura") == "plugin:aura"


def test_unknown_candidate_blocks() -> None:
    items = _run("plugin:nope")
    assert _codes(items) == ["candidate_unknown"]
    assert items[0].level is PreflightLevel.BLOCK
    assert _codes(_run("core", report=None)) == ["candidate_unknown"]
    assert _codes(_run("plugin:aura", "9.9.9")) == ["candidate_unknown"]


def test_installed_release_is_already_installed() -> None:
    items = _run("plugin:aura", report=_report(candidates=[_plugin(installed="1.1.0")]))
    assert _codes(items) == ["already_installed"]
    assert items[0].level is PreflightLevel.BLOCK


def test_installable_plugin_is_ready() -> None:
    items = _run("plugin:aura")
    assert _codes(items) == ["ready"]
    assert items[0].level is PreflightLevel.OK and "1.1.0" in items[0].message
    assert _codes(_run("aura", "1.1.0")) == ["ready"]


def _forcing(blocker: ApplyBlocker) -> dict[str, Any]:
    """Inputs under which ``apply_status`` reports ``blocker``."""
    cand = _plugin()
    over: dict[str, Any] = {}
    if blocker is ApplyBlocker.APPLY_DISABLED:
        over["config"] = CFG.model_copy(update={"enabled": False})
    elif blocker is ApplyBlocker.NOT_HOST_INSTALL:
        over["method"] = "helm"
    elif blocker is ApplyBlocker.OVERLAY_UNCONFIGURED:
        over["heartbeat"] = _heartbeat(overlay_writable=False)
    elif blocker is ApplyBlocker.UPDATER_OFFLINE:
        over["heartbeat"] = None
    elif blocker is ApplyBlocker.UPDATER_MISMATCH:
        over["heartbeat"] = _heartbeat(core_version="0.0.1")
    elif blocker is ApplyBlocker.UNSIGNED_RELEASE:
        cand = cand.model_copy(update={"signed_assets": None})
    elif blocker is ApplyBlocker.SIGNATURE_FAILED:
        cand = cand.model_copy(
            update={
                "signed_assets": SignedAssets(
                    verified=False, refusal="signature_invalid"
                )
            }
        )
    elif blocker is ApplyBlocker.NEEDS_ENVIRONMENT_UPDATE:
        cand = cand.model_copy(
            update={
                "signed_assets": SignedAssets(
                    verified=False, refusal="needs_environment_update"
                )
            }
        )
    elif blocker is ApplyBlocker.HOST_BUILD_REQUIRED:
        cand = cand.model_copy(
            update={
                "signed_assets": SignedAssets(
                    verified=True, tarball_sha256="a" * 64, host_build_required=True
                )
            }
        )
    elif blocker is ApplyBlocker.RUN_ACTIVE:
        over["active_run"] = ApplyRun(
            id="pinstall-20261113T120000Z-0000abcd",
            kind=RunKind.UPDATE,
            plugin="aura",
            from_version="1.0.0",
            to_version="1.1.0",
            tarball_sha256=None,
            requested_by="x",
            requested_at=NOW,
            state=RunState.PREPARING,
            updated_at=NOW,
        )
    over["report"] = _report(candidates=[cand])
    return over


_PLUGIN_BLOCKERS = [b for b in ApplyBlocker if b not in CORE_ONLY_BLOCKERS]


@pytest.mark.parametrize("blocker", _PLUGIN_BLOCKERS)
def test_each_apply_blocker_becomes_a_block_item(blocker: ApplyBlocker) -> None:
    over = _forcing(blocker)
    items = _run("plugin:aura", **over)
    by_code = {i.code: i for i in items}
    code = f"blocker_{blocker.value}"
    assert code in by_code, _codes(items)
    assert by_code[code].level is PreflightLevel.BLOCK
    assert by_code[code].message  # a sentence, not just the code
    assert "ready" not in by_code


def test_blocker_items_match_apply_status() -> None:
    """The pre-flight reports exactly the verdict the console shows."""
    over = _forcing(ApplyBlocker.UPDATER_OFFLINE)
    cand = over["report"].candidates[0]
    status = apply_status(
        cand,
        config=CFG,
        method="source",
        heartbeat=None,
        active_run=None,
        core_version=CORE,
        now=NOW,
    )
    items = _run("plugin:aura", **over)
    blocks = [c for c in _codes(items) if c.startswith("blocker_")]
    assert blocks == [f"blocker_{b.value}" for b in status.blockers]


def test_core_subject_lists_incompatible_plugins_as_warn() -> None:
    bounds = [
        PluginBoundsIssue(plugin="old", max_core_version="1.3.9"),
        PluginBoundsIssue(plugin="fine", min_core_version="1.0.0"),
    ]
    items = _run("core", report=_report(system=_notice()), bounds=bounds)
    item = next(i for i in items if i.code == "plugin_incompatible")
    assert item.level is PreflightLevel.WARN
    assert "old" in item.message and "fine" not in item.message


def test_core_compatibility_unknown_is_a_warn() -> None:
    items = _run("core", report=_report(system=_notice()), bounds=None)
    assert "plugin_compatibility_unknown" in _codes(items)


def test_distribution_core_subject_reads_the_distribution_notice() -> None:
    dist = _notice(installed="1.14.0", latest="1.15.0").model_copy(
        update={"component": "distribution"}
    )
    items = _run(
        "core",
        report=_report(system=_notice(), distribution=dist),
        distribution=True,
    )
    assert "plugin_compatibility_unknown" in _codes(items)
    # no verified distribution manifest was passed in: unsigned
    assert "blocker_unsigned_release" in _codes(items)
    assert next(i for i in items if i.code == "blocker_unsigned_release")
    assert _codes(
        _run("core", report=_report(system=_notice()), distribution=True)
    ) == ["candidate_unknown"]


def test_major_jump_blocks_core() -> None:
    items = _run("core", report=_report(system=_notice(latest="2.0.0")))
    major = next(i for i in items if i.code == "major_jump")
    assert major.level is PreflightLevel.BLOCK
    plugin = _run("plugin:aura", report=_report(candidates=[_plugin(latest="2.0.0")]))
    assert (
        next(i for i in plugin if i.code == "major_jump").level is PreflightLevel.WARN
    )


def test_security_advisory_is_informational_ok() -> None:
    notice = _notice(security=True, severity="high")
    items = _run("core", report=_report(system=notice))
    item = next(i for i in items if i.code == "security_advisory")
    assert item.level is PreflightLevel.OK and "high" in item.message


def test_queue_probe_error_is_a_warn_not_a_raise() -> None:
    def down() -> int:
        raise ConnectionError("redis down")

    items = _run("plugin:aura", queue_probe=down)
    assert _codes(items) == ["queue_probe_unavailable", "ready"]
    assert items[0].level is PreflightLevel.WARN


def test_busy_queue_warns() -> None:
    items = _run("plugin:aura", queue_probe=lambda: 3)
    assert items[0].code == "queue_busy" and "3" in items[0].message


def test_disk_low_warns() -> None:
    seen: list[Path] = []

    def free(path: Path) -> int:
        seen.append(path)
        return 100 * 1024 * 1024

    items = _run("plugin:aura", disk_free=free)
    assert _codes(items) == ["disk_low", "ready"]
    assert seen == [Path("/srv/overlay")]


def test_disk_probe_error_is_a_warn() -> None:
    def broken(_p: Path) -> int:
        raise OSError("gone")

    assert _codes(_run("plugin:aura", disk_free=broken)) == [
        "disk_probe_unavailable",
        "ready",
    ]


def test_boot_report_probe_failures_are_warns() -> None:
    def unreadable() -> list[str] | None:
        raise PermissionError("denied")

    assert _codes(_run("plugin:aura", boot_probe=unreadable)) == [
        "boot_report_unavailable",
        "ready",
    ]
    assert _codes(_run("plugin:aura", boot_probe=lambda: None)) == [
        "no_boot_report",
        "ready",
    ]
    assert _codes(_run("plugin:aura", boot_probe=lambda: ["aura"])) == ["ready"]


def test_no_heartbeat_never_raises() -> None:
    items = _run("plugin:aura", heartbeat=None, queue_probe=lambda: 1)
    assert "blocker_updater_offline" in _codes(items)
    assert "queue_busy" in _codes(items)


def test_ready_only_without_blocks() -> None:
    items = _run("plugin:aura", queue_probe=lambda: 2, disk_free=lambda _p: 0)
    assert _codes(items)[-1] == "ready"
    blocked = _run("plugin:aura", **_forcing(ApplyBlocker.RUN_ACTIVE))
    assert "ready" not in _codes(blocked)
