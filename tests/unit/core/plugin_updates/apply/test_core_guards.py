"""Core install guards: the blockers, their order, and the clean-tree rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from baselith.core.config.plugin_update_apply import UpdateApplyConfig
from baselith.core.plugin_updates.apply.drivers._core_guards import (
    CORE_ONLY_BLOCKERS,
    CoreRelease,
    core_blockers,
    tree_is_dirty,
)
from baselith.core.plugin_updates.apply.models import (
    CORE_CLAIM,
    CORE_SUBJECT,
    ApplyRun,
    RunKind,
    RunState,
    UpdaterHeartbeat,
)
from baselith.core.plugin_updates.models import ApplyBlocker

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
LOCK = "b" * 64
CFG = UpdateApplyConfig(
    enabled=True,
    core_enabled=True,
    restart_command=["true"],
    backup_command=["/usr/local/bin/backup"],
    updater_restart_command=["true"],
    state_dir=Path("/tmp/x-state"),
)
B = ApplyBlocker


def _release(**over: Any) -> CoreRelease:
    base: dict[str, Any] = {
        "version": "1.15.0",
        "commit_sha": "a" * 40,
        "lock_sha256": LOCK,
        "migrations": "none",
    }
    base.update(over)
    return CoreRelease(**base)


def _hb(**over: Any) -> UpdaterHeartbeat:
    base: dict[str, Any] = {
        "pid": 1,
        "started_at": NOW - timedelta(minutes=1),
        "at": NOW,
        "core_version": "1.3.1",
        "enabled": True,
        "overlay_root": None,
        "overlay_writable": False,
        "restart_configured": True,
        "framework_version": "1.14.0",
    }
    base.update(over)
    return UpdaterHeartbeat(**base)


def _active() -> ApplyRun:
    return ApplyRun(
        id="pinstall-20261114T120000Z-0000abcd",
        kind=RunKind.UPDATE,
        plugin=CORE_CLAIM,
        subject=CORE_SUBJECT,
        from_version="1.14.0",
        to_version="1.15.0",
        tarball_sha256="a" * 40,
        requested_by="cli:op",
        requested_at=NOW,
        state=RunState.PREPARING,
        updated_at=NOW,
    )


def _blockers(release: CoreRelease | None = None, **over: Any) -> list[ApplyBlocker]:
    args: dict[str, Any] = {
        "config": CFG,
        "installed_version": "1.14.0",
        "installed_lock_sha256": LOCK,
        "tree_dirty": False,
        "install_method": "source",
        "heartbeat": _hb(),
        "active_run": None,
        "core_version": "1.3.1",
        "now": NOW,
        "environ": {},
        "root": Path("/nonexistent-root"),
    }
    args.update(over)
    return core_blockers(_release() if release is None else release, **args)


def test_clean_signed_release_has_no_blocker() -> None:
    assert _blockers() == []


def test_core_switch_is_off_by_default() -> None:
    assert UpdateApplyConfig().core_enabled is False
    assert _blockers(config=CFG.model_copy(update={"core_enabled": False})) == [
        B.CORE_DISABLED
    ]


def test_pip_install_is_unsupported() -> None:
    assert _blockers(install_method="pip") == [B.CORE_INSTALL_UNSUPPORTED]
    assert _blockers(install_method="custom") == [B.CORE_INSTALL_UNSUPPORTED]


def test_kubernetes_is_not_a_host_install() -> None:
    blockers = _blockers(environ={"KUBERNETES_SERVICE_HOST": "10.0.0.1"})
    assert blockers == [B.NOT_HOST_INSTALL]


def test_kill_switch_or_disabled_updater_is_apply_disabled() -> None:
    assert _blockers(config=CFG.model_copy(update={"enabled": False})) == [
        B.APPLY_DISABLED
    ]
    assert _blockers(heartbeat=_hb(enabled=False)) == [B.APPLY_DISABLED]
    assert _blockers(heartbeat=_hb(restart_configured=False)) == [B.APPLY_DISABLED]


def test_signed_min_core_version_is_enforced() -> None:
    # The oldest installed public core (CORE_VERSION) the release may go over.
    for floor in ("1.3.0", "1.3.1"):
        assert _blockers(_release(min_core_version=floor), core_version="1.3.1") == []
    assert _blockers(_release(min_core_version="1.4.0"), core_version="1.3.1") == [
        B.CORE_TOO_OLD
    ]
    # An unreadable bound never passes as "satisfied".
    for bad in ("not-a-version", ""):
        assert _blockers(_release(min_core_version=bad), core_version="1.3.1") == [
            B.CORE_TOO_OLD
        ]
    unreadable_core = _blockers(
        _release(min_core_version="1.0.0"),
        core_version="x",
        heartbeat=_hb(core_version="x"),
    )
    assert unreadable_core == [B.CORE_TOO_OLD]


def test_core_install_needs_the_updater_restart_command() -> None:
    # Without it every core install would end finalizing with the updater
    # running the old code over the new tree, holding the core's claim.
    cfg = CFG.model_copy(update={"updater_restart_command": []})
    assert _blockers(config=cfg) == [B.UPDATER_RESTART_UNCONFIGURED]
    assert B.UPDATER_RESTART_UNCONFIGURED in CORE_ONLY_BLOCKERS


def test_missing_or_stale_heartbeat_is_offline() -> None:
    assert _blockers(heartbeat=None) == [B.UPDATER_OFFLINE]
    stale = _hb(at=NOW - timedelta(minutes=10))
    assert _blockers(heartbeat=stale) == [B.UPDATER_OFFLINE]


def test_updater_on_another_version_is_a_mismatch() -> None:
    assert _blockers(heartbeat=_hb(core_version="1.2.0")) == [B.UPDATER_MISMATCH]
    assert _blockers(heartbeat=_hb(framework_version="1.13.0")) == [B.UPDATER_MISMATCH]
    assert _blockers(heartbeat=_hb(framework_version=None)) == []


def test_backup_command_is_required() -> None:
    cfg = CFG.model_copy(update={"backup_command": []})
    assert _blockers(config=cfg) == [B.BACKUP_UNCONFIGURED]


def test_dirty_or_unknown_tree_blocks() -> None:
    assert _blockers(tree_dirty=True) == [B.TREE_DIRTY]
    assert _blockers(tree_dirty=None) == [B.TREE_DIRTY]


def test_changed_or_unreadable_lock_needs_an_environment_update() -> None:
    assert _blockers(installed_lock_sha256="c" * 64) == [B.NEEDS_ENVIRONMENT_UPDATE]
    assert _blockers(installed_lock_sha256=None) == [B.NEEDS_ENVIRONMENT_UPDATE]


def test_breaking_migration_blocks_expand_does_not() -> None:
    assert _blockers(_release(migrations="breaking")) == [B.BREAKING_MIGRATION]
    assert _blockers(_release(migrations="expand")) == []


def test_major_jump_blocks() -> None:
    assert _blockers(_release(version="2.0.0")) == [B.MAJOR_JUMP]
    assert _blockers(_release(version="not-a-version")) == [B.MAJOR_JUMP]


def test_active_run_blocks() -> None:
    assert _blockers(active_run=_active()) == [B.RUN_ACTIVE]


def test_no_verified_release_is_unsigned() -> None:
    args: dict[str, Any] = {
        "config": CFG,
        "installed_version": "1.14.0",
        "installed_lock_sha256": LOCK,
        "tree_dirty": False,
        "install_method": "source",
        "heartbeat": _hb(),
        "active_run": None,
        "core_version": "1.3.1",
        "now": NOW,
        "environ": {},
        "root": Path("/nonexistent-root"),
    }
    assert core_blockers(None, **args) == [B.UNSIGNED_RELEASE]


def test_blockers_are_in_console_order() -> None:
    cfg = CFG.model_copy(
        update={"core_enabled": False, "enabled": False, "backup_command": []}
    )
    blockers = _blockers(
        _release(version="2.0.0", migrations="breaking"),
        config=cfg,
        install_method="pip",
        heartbeat=_hb(framework_version="1.0.0"),
        tree_dirty=True,
        installed_lock_sha256="c" * 64,
        active_run=_active(),
    )
    assert blockers == [
        B.CORE_DISABLED,
        B.CORE_INSTALL_UNSUPPORTED,
        B.APPLY_DISABLED,
        B.UPDATER_MISMATCH,
        B.BACKUP_UNCONFIGURED,
        B.TREE_DIRTY,
        B.NEEDS_ENVIRONMENT_UPDATE,
        B.BREAKING_MIGRATION,
        B.MAJOR_JUMP,
        B.RUN_ACTIVE,
    ]


def test_core_blockers_never_require_an_overlay() -> None:
    hb = _hb(overlay_root=None, overlay_writable=False)
    blockers = _blockers(heartbeat=hb)
    assert B.OVERLAY_UNCONFIGURED not in blockers and blockers == []


def test_core_only_blockers_are_the_core_ones() -> None:
    assert {b.value for b in CORE_ONLY_BLOCKERS} == {
        "backup_unconfigured",
        "updater_restart_unconfigured",
        "core_too_old",
        "tree_dirty",
        "breaking_migration",
        "major_jump",
        "core_disabled",
        "core_install_unsupported",
    }


def test_untracked_files_do_not_make_the_tree_dirty() -> None:
    assert not tree_is_dirty("?? data/cache.db\n?? logs/x.log\n", [])
    assert not tree_is_dirty("", [])
    assert tree_is_dirty(" M baselith/core/x.py\n", [])
    assert tree_is_dirty("D  uv.lock\n", [])


def test_ignored_path_prefix_is_matched_on_a_path_boundary() -> None:
    ignored = ["ui/dist"]
    assert not tree_is_dirty(" M ui/dist/a.js\n", ignored)
    assert not tree_is_dirty(" M ui/dist\n", ignored)
    assert tree_is_dirty(" M ui/distant/a.js\n", ignored)
    assert not tree_is_dirty(" M ui/dist/a.js\n", ["ui/dist/"])


def test_a_rename_counts_both_sides() -> None:
    assert tree_is_dirty("R  ui/dist/a.js -> src/a.js\n", ["ui/dist"])
    assert not tree_is_dirty("R  ui/dist/a.js -> ui/dist/b.js\n", ["ui/dist"])


def test_blank_backup_command_means_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPDATE_APPLY_BACKUP_COMMAND", "")
    cfg = UpdateApplyConfig()
    assert cfg.backup_command == [] and not cfg.backup_configured
