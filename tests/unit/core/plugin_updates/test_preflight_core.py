"""Pre-flight of the core subject: the core guards as items, with what is known."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from baselith.core.plugin_updates.apply.drivers._core_guards import CoreRelease
from baselith.core.plugin_updates.preflight import CoreState, PreflightLevel

from .test_preflight import CFG, NOW, _codes, _heartbeat, _notice, _report, _run

LOCK = "b" * 64
CORE_CFG = CFG.model_copy(
    update={
        "core_enabled": True,
        "backup_command": ["/usr/local/bin/backup"],
        "updater_restart_command": ["systemctl", "--no-block", "restart", "u"],
    }
)


@pytest.fixture(autouse=True)
def _host_install(monkeypatch: pytest.MonkeyPatch) -> None:
    for target in (
        "baselith.core.plugin_updates.apply.eligibility.host_install_ok",
        "baselith.core.plugin_updates.apply.drivers._core_guards.host_install_ok",
    ):
        monkeypatch.setattr(
            target, lambda method, **_: method in ("source", "pip", "custom")
        )


def _state(**over: Any) -> CoreState:
    base: dict[str, Any] = {
        "installed_version": "1.3.1",
        "installed_lock_sha256": LOCK,
        "tree_dirty": False,
        "release": CoreRelease(
            version="1.4.0", commit_sha="a" * 40, lock_sha256=LOCK, migrations="none"
        ),
    }
    base.update(over)
    return CoreState(**base)


def _core(**over: Any):
    args: dict[str, Any] = {
        "report": _report(system=_notice()),
        "config": CORE_CFG,
        "core": _state(),
    }
    args.update(over)
    return _run("core", **args)


def test_verified_clean_core_release_is_ready() -> None:
    assert _codes(_core()) == ["ready"]


def test_core_preflight_lists_every_core_blocker() -> None:
    cfg = CORE_CFG.model_copy(
        update={
            "core_enabled": False,
            "enabled": False,
            "backup_command": [],
            "updater_restart_command": [],
        }
    )
    release = CoreRelease(
        version="2.0.0",
        commit_sha="a" * 40,
        lock_sha256="c" * 64,
        migrations="breaking",
    )
    items = _core(
        report=_report(system=_notice(latest="2.0.0")),
        config=cfg,
        method="pip",
        core=_state(tree_dirty=True, release=release),
        heartbeat=_heartbeat(framework_version="0.9.0"),
    )
    blocks = [c for c in _codes(items) if c.startswith("blocker_")]
    assert blocks == [
        "blocker_core_disabled",
        "blocker_core_install_unsupported",
        "blocker_apply_disabled",
        "blocker_updater_mismatch",
        "blocker_backup_unconfigured",
        "blocker_updater_restart_unconfigured",
        "blocker_tree_dirty",
        "blocker_needs_environment_update",
        "blocker_breaking_migration",
        "blocker_major_jump",
    ]
    assert "major_jump" not in _codes(items)  # said once, as the blocker
    assert all(i.message for i in items)
    assert "ready" not in _codes(items)


def test_core_preflight_never_asks_for_an_overlay() -> None:
    items = _core(heartbeat=_heartbeat(overlay_root=None, overlay_writable=False))
    assert "blocker_overlay_unconfigured" not in _codes(items)
    assert _codes(items)[-1] == "ready"


def test_no_core_state_is_unknown_and_blocks() -> None:
    items = _core(core=None)
    codes = _codes(items)
    assert "blocker_unsigned_release" in codes and "blocker_tree_dirty" in codes
    tree = next(i for i in items if i.code == "blocker_tree_dirty")
    assert "could not be read" in tree.message


def test_refused_manifest_says_why() -> None:
    items = _core(core=_state(release=None, release_detail="signature_invalid"))
    item = next(i for i in items if i.code == "blocker_unsigned_release")
    assert item.level is PreflightLevel.BLOCK and "signature_invalid" in item.message


def test_unreadable_lock_is_explained() -> None:
    items = _core(core=_state(installed_lock_sha256=None))
    item = next(i for i in items if i.code == "blocker_needs_environment_update")
    assert "could not be read" in item.message


def test_disk_probe_uses_the_state_dir_without_an_overlay() -> None:
    seen: list[Path] = []

    def disk(path: Path) -> int:
        seen.append(path)
        return 10 * 1024**3

    _core(heartbeat=_heartbeat(overlay_root=None), disk_free=disk)
    assert seen == [CORE_CFG.state_dir]
    assert NOW.tzinfo is not None


def test_unchecked_release_is_a_warn_not_unsigned() -> None:
    items = _core(core=_state(release=None, release_checked=False))
    codes = _codes(items)
    assert "blocker_unsigned_release" not in codes
    assert "release_unverified" in codes and codes[-1] == "ready"


def test_local_core_state_reads_the_checkout(tmp_path: Path) -> None:
    from baselith.core.plugin_updates._core_state import local_core_state
    from baselith.core.plugin_updates.apply.drivers._git import GitResult

    (tmp_path / "uv.lock").write_text("version = 1\n")

    def git(argv, *, cwd, timeout):  # type: ignore[no-untyped-def]
        return GitResult(0, " M ui/dist/a.js\n")

    state = local_core_state(
        CORE_CFG.model_copy(update={"ignored_paths": ["ui/dist"]}),
        git=git,
        root=tmp_path,
    )
    assert state.tree_dirty is False and state.installed_lock_sha256
    assert state.release_checked is False and state.release is None
