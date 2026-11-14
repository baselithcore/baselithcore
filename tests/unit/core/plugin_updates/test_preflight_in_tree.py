"""Pre-flight: an unsigned release of a plugin shipped in this tree is no fault.

A plugin that lives in the host's own tree (the checkout, or the installed
distribution's ``plugins`` package) and is not an overlay install arrives with
the tree's next release. Its newer mirror release carrying no signed assets is
expected, so it reads as an ``info`` item, never as a block; every other
blocker stays a block, and an overlay install or the core keeps the block.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from baselith.core.cli.commands import _update_preflight
from baselith.core.plugin_updates._preflight_live import live_preflight, ships_with_tree
from baselith.core.plugin_updates.apply.store import RunStore
from baselith.core.plugin_updates.models import ApplyBlocker
from baselith.core.plugin_updates.preflight import PreflightItem, PreflightLevel

from .test_preflight import CFG, _codes, _forcing, _notice, _plugin, _report, _run


def _unsigned(**over: object) -> dict[str, object]:
    args = _forcing(ApplyBlocker.UNSIGNED_RELEASE)
    args.update(over)
    return args


def test_unsigned_in_tree_plugin_is_info_not_block() -> None:
    items = _run("plugin:aura", **_unsigned(in_tree=True))
    by_code = {i.code: i for i in items}
    assert "blocker_unsigned_release" not in by_code
    item = by_code["in_tree_release"]
    assert item.level is PreflightLevel.INFO
    assert "next core release" in item.message
    assert "one-click" in item.message
    assert not any(i.level is PreflightLevel.BLOCK for i in items)
    # It still cannot be installed from here: no "ready".
    assert "ready" not in by_code


def test_in_tree_wording_names_the_distribution_in_a_distribution() -> None:
    items = _run("plugin:aura", **_unsigned(in_tree=True, distribution=True))
    item = next(i for i in items if i.code == "in_tree_release")
    assert "next distribution release" in item.message


def test_unsigned_overlay_plugin_still_blocks() -> None:
    items = _run("plugin:aura", **_unsigned())  # in_tree defaults to False
    item = next(i for i in items if i.code == "blocker_unsigned_release")
    assert item.level is PreflightLevel.BLOCK
    assert "in_tree_release" not in _codes(items)


@pytest.mark.parametrize(
    "blocker",
    [
        ApplyBlocker.SIGNATURE_FAILED,
        ApplyBlocker.UPDATER_MISMATCH,
        ApplyBlocker.UPDATER_OFFLINE,
        ApplyBlocker.NEEDS_ENVIRONMENT_UPDATE,
    ],
)
def test_real_blockers_of_an_in_tree_plugin_stay_blocks(blocker: ApplyBlocker) -> None:
    over = _forcing(blocker)
    items = _run("plugin:aura", in_tree=True, **over)
    item = next(i for i in items if i.code == f"blocker_{blocker.value}")
    assert item.level is PreflightLevel.BLOCK
    assert "ready" not in _codes(items)


def test_other_blockers_beside_an_in_tree_unsigned_release_stay_blocks() -> None:
    items = _run("plugin:aura", **_unsigned(in_tree=True, heartbeat=None))
    levels = {i.code: i.level for i in items}
    assert levels["blocker_updater_offline"] is PreflightLevel.BLOCK
    assert levels["in_tree_release"] is PreflightLevel.INFO


def test_signed_in_tree_plugin_is_still_ready() -> None:
    assert _codes(_run("plugin:aura", in_tree=True)) == ["ready"]


def test_core_subject_ignores_in_tree() -> None:
    items = _run("core", report=_report(system=_notice()), in_tree=True)
    assert "in_tree_release" not in _codes(items)


# -- which plugins ship with the tree ---------------------------------------


def _plugin_dir(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "manifest.yaml").write_text(f"name: {name}\nversion: 1.0.0\n")
    return path


def test_ships_with_tree_for_a_plugin_of_the_tree(tmp_path: Path) -> None:
    tree = tmp_path / "plugins"
    _plugin_dir(tree, "aura")
    assert ships_with_tree("aura", tree=tree, loader_root=tree, overlaid=())


def test_ships_with_tree_is_false_for_an_overlay_install(tmp_path: Path) -> None:
    tree = tmp_path / "plugins"
    _plugin_dir(tree, "aura")
    assert not ships_with_tree("aura", tree=tree, loader_root=tree, overlaid={"aura"})


def test_ships_with_tree_is_false_outside_the_tree(tmp_path: Path) -> None:
    tree = tmp_path / "plugins"
    tree.mkdir()
    user_root = tmp_path / "user-plugins"
    _plugin_dir(user_root, "aura")  # a `baselith plugin add` install
    assert not ships_with_tree("aura", tree=tree, loader_root=user_root, overlaid=())
    # The loader reads its own copy, not the tree's: not the tree's either.
    _plugin_dir(tree, "aura")
    assert not ships_with_tree("aura", tree=tree, loader_root=user_root, overlaid=())


def test_ships_with_tree_rejects_odd_names_and_links(tmp_path: Path) -> None:
    tree = tmp_path / "plugins"
    real = _plugin_dir(tmp_path / "elsewhere", "aura")
    tree.mkdir()
    (tree / "aura").symlink_to(real, target_is_directory=True)
    for name in ("aura", "", "../aura", ".hidden"):
        assert not ships_with_tree(name, tree=tree, loader_root=tree, overlaid=())


def test_live_preflight_asks_whether_the_plugin_ships_with_the_tree(
    tmp_path: Path,
) -> None:
    cand = _plugin().model_copy(update={"signed_assets": None})
    asked: list[str] = []

    def in_tree(name: str) -> bool:
        asked.append(name)
        return True

    items = live_preflight(
        "plugin:aura",
        None,
        report=_report(candidates=[cand]),
        config=CFG.model_copy(update={"state_dir": tmp_path}),
        method="source",
        bundled_root=tmp_path,
        core_version="1.3.1",
        distribution=True,
        store=RunStore(tmp_path),
        queue_probe=lambda: 0,
        in_tree=in_tree,
    )
    assert asked == ["aura"]
    assert "in_tree_release" in _codes(items)


def test_live_preflight_treats_a_failing_probe_as_not_in_tree(tmp_path: Path) -> None:
    cand = _plugin().model_copy(update={"signed_assets": None})

    def broken(_name: str) -> bool:
        raise OSError("unreadable")

    items = live_preflight(
        "plugin:aura",
        None,
        report=_report(candidates=[cand]),
        config=CFG.model_copy(update={"state_dir": tmp_path}),
        method="source",
        bundled_root=tmp_path,
        core_version="1.3.1",
        distribution=False,
        store=RunStore(tmp_path),
        queue_probe=lambda: 0,
        in_tree=broken,
    )
    assert "blocker_unsigned_release" in _codes(items)


def test_cli_preflight_exits_1_when_only_an_info_item_stands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    items = [
        PreflightItem(code="in_tree_release", level=PreflightLevel.INFO, message="x")
    ]
    monkeypatch.setattr(_update_preflight, "_preflight_inputs", lambda *_a: items)
    assert _update_preflight.run_preflight("plugin:aura", None, "text") == 1
    assert "[info] in_tree_release: x" in capsys.readouterr().out
    ready = [PreflightItem(code="ready", level=PreflightLevel.OK, message="ok")]
    monkeypatch.setattr(_update_preflight, "_preflight_inputs", lambda *_a: ready)
    assert _update_preflight.run_preflight("plugin:aura", None, "text") == 0
