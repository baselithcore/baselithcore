from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.plugin_updates.apply.swap import current_target, point_to, prune_store


def _store(root: Path, *names: str) -> None:
    for name in names:
        (root / ".store" / name).mkdir(parents=True)


def test_no_link_means_bundled(tmp_path: Path) -> None:
    assert current_target(tmp_path, "demo") is None


def test_point_to_and_back(tmp_path: Path) -> None:
    _store(tmp_path, "demo-1.1.0", "demo-1.2.0")
    point_to(tmp_path, "demo", "demo-1.1.0", run_id="r1")
    point_to(tmp_path, "demo", "demo-1.2.0", run_id="r2")
    assert current_target(tmp_path, "demo") == "demo-1.2.0"
    assert os.readlink(tmp_path / "demo") == os.path.join(".store", "demo-1.2.0")
    point_to(tmp_path, "demo", None, run_id="r3")
    assert not (tmp_path / "demo").exists() and current_target(tmp_path, "demo") is None
    assert not list(tmp_path.glob(".demo.tmp-*"))


def test_swap_is_atomic_over_an_existing_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store(tmp_path, "demo-1.1.0", "demo-1.2.0")
    point_to(tmp_path, "demo", "demo-1.1.0", run_id="r1")
    seen: list[str] = []
    real = os.replace

    def spy(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        seen.append(os.readlink(dst))
        real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    point_to(tmp_path, "demo", "demo-1.2.0", run_id="r2")
    assert seen == [os.path.join(".store", "demo-1.1.0")]


def test_plain_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "demo").mkdir()
    with pytest.raises(ValueError, match="not a link"):
        current_target(tmp_path, "demo")
    with pytest.raises(ValueError):
        point_to(tmp_path, "demo", None, run_id="r")


def test_target_must_exist_in_store(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        point_to(tmp_path, "demo", "demo-9.9.9", run_id="r")


@pytest.mark.parametrize("bad", ["../x", "a/b", "..", ".", ""])
def test_target_must_be_a_plain_store_name(tmp_path: Path, bad: str) -> None:
    (tmp_path / "x").mkdir()
    with pytest.raises(ValueError):
        point_to(tmp_path, "demo", bad, run_id="r")


def test_escaping_link_is_refused(tmp_path: Path) -> None:
    os.symlink("/etc", tmp_path / "demo")
    with pytest.raises(ValueError, match="escapes"):
        current_target(tmp_path, "demo")
    os.replace(tmp_path / "demo", tmp_path / "gone")
    os.symlink(os.path.join(".store", "..", "x"), tmp_path / "demo")
    with pytest.raises(ValueError, match="escapes"):
        current_target(tmp_path, "demo")


def test_prune_keeps_active_previous_and_limit(tmp_path: Path) -> None:
    _store(
        tmp_path, "demo-1.0.0", "demo-1.1.0", "demo-1.2.0", "demo-1.10.0", "other-1.0.0"
    )
    removed = prune_store(
        tmp_path, "demo", keep={"demo-1.10.0", "demo-1.1.0"}, keep_versions=2
    )
    assert sorted(p.name for p in removed) == ["demo-1.0.0", "demo-1.2.0"]
    assert (tmp_path / ".store" / "other-1.0.0").is_dir()


def test_prune_never_removes_the_linked_target(tmp_path: Path) -> None:
    _store(tmp_path, "demo-1.0.0", "demo-1.1.0", "demo-1.2.0")
    point_to(tmp_path, "demo", "demo-1.0.0", run_id="r")
    removed = prune_store(tmp_path, "demo", keep=set(), keep_versions=1)
    assert (tmp_path / ".store" / "demo-1.0.0").is_dir()
    assert sorted(p.name for p in removed) == ["demo-1.1.0", "demo-1.2.0"]


def test_prune_does_not_follow_symlinks_in_store(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    _store(tmp_path, "demo-1.0.0")
    os.symlink(outside, tmp_path / ".store" / "demo-0.9.0")
    prune_store(tmp_path, "demo", keep=set(), keep_versions=0)
    assert (outside / "keep.txt").exists()


def test_prune_removes_only_stale_scratch(tmp_path: Path) -> None:
    _store(tmp_path, ".staging-old", ".staging-new", ".trash-old")
    old = time.time() - 2 * 86_400
    for name in (".staging-old", ".trash-old"):
        os.utime(tmp_path / ".store" / name, (old, old))
    removed = prune_store(tmp_path, "demo", keep=set(), keep_versions=2)
    assert sorted(p.name for p in removed) == [".staging-old", ".trash-old"]
    assert (tmp_path / ".store" / ".staging-new").is_dir()


def test_prune_without_store_is_a_noop(tmp_path: Path) -> None:
    assert prune_store(tmp_path, "demo", keep=set(), keep_versions=2) == []


def test_absolute_link_is_read_and_survives_prune(tmp_path: Path) -> None:
    _store(tmp_path, "demo-1.0.0", "demo-1.1.0")
    os.symlink(tmp_path / ".store" / "demo-1.0.0", tmp_path / "demo")
    assert current_target(tmp_path, "demo") == "demo-1.0.0"
    prune_store(tmp_path, "demo", keep=set(), keep_versions=1)
    assert (tmp_path / ".store" / "demo-1.0.0").is_dir()


def test_prune_fails_closed_on_unreadable_link(tmp_path: Path) -> None:
    _store(tmp_path, "demo-1.0.0", "demo-1.1.0")
    os.symlink("/etc", tmp_path / "demo")
    assert prune_store(tmp_path, "demo", keep=set(), keep_versions=0) == []
    assert (tmp_path / ".store" / "demo-1.0.0").is_dir()


def test_prerelease_is_pruned_as_older(tmp_path: Path) -> None:
    _store(tmp_path, "demo-1.0.0-rc.1", "demo-1.0.0")
    removed = prune_store(tmp_path, "demo", keep=set(), keep_versions=1)
    assert [p.name for p in removed] == ["demo-1.0.0-rc.1"]


def test_unparseable_entry_survives(tmp_path: Path) -> None:
    _store(tmp_path, "demo-latest", "demo-1.0.0")
    prune_store(tmp_path, "demo", keep=set(), keep_versions=0)
    assert (tmp_path / ".store" / "demo-latest").is_dir()


def test_scratch_vanishing_concurrently_is_tolerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store(tmp_path, ".staging-x")
    real = Path.stat

    def flaky(self: Path, *a: object, **k: object) -> os.stat_result:
        if self.name == ".staging-x":
            raise FileNotFoundError(self)
        return real(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", flaky)
    assert prune_store(tmp_path, "demo", keep=set(), keep_versions=1) == []


def test_temp_link_removed_when_replace_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store(tmp_path, "demo-1.0.0")

    def boom(src: object, dst: object) -> None:
        raise OSError("nope")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        point_to(tmp_path, "demo", "demo-1.0.0", run_id="r")
    assert not list(tmp_path.glob(".demo.tmp-*"))


def test_run_id_and_target_prefix_validated(tmp_path: Path) -> None:
    _store(tmp_path, "demo-1.0.0", "other-1.0.0")
    with pytest.raises(ValueError):
        point_to(tmp_path, "demo", "demo-1.0.0", run_id="../x")
    with pytest.raises(ValueError):
        point_to(tmp_path, "demo", "other-1.0.0", run_id="r")


def test_link_to_another_plugins_entry_is_refused(tmp_path: Path) -> None:
    _store(tmp_path, "demo-1.0.0", "demo-1.1.0", "other-1.0.0")
    os.symlink(os.path.join(".store", "other-1.0.0"), tmp_path / "demo")
    with pytest.raises(ValueError, match="another plugin"):
        current_target(tmp_path, "demo")
    assert prune_store(tmp_path, "demo", keep=set(), keep_versions=0) == []
    assert (tmp_path / ".store" / "demo-1.0.0").is_dir()
    assert (tmp_path / ".store" / "demo-1.1.0").is_dir()


def test_symlink_loop_is_a_value_error_and_prune_skips(tmp_path: Path) -> None:
    _store(tmp_path, "demo-1.0.0", "demo-1.1.0")
    os.symlink("demo", tmp_path / "demo")  # demo -> demo
    with pytest.raises(ValueError):
        current_target(tmp_path, "demo")
    assert prune_store(tmp_path, "demo", keep=set(), keep_versions=0) == []
    assert (tmp_path / ".store" / "demo-1.0.0").is_dir()


def test_link_to_a_non_version_suffix_is_refused(tmp_path: Path) -> None:
    _store(tmp_path, "demo-1.0.0", "demo-extra-1.0.0")
    os.symlink(os.path.join(".store", "demo-extra-1.0.0"), tmp_path / "demo")
    with pytest.raises(ValueError):
        current_target(tmp_path, "demo")
    assert prune_store(tmp_path, "demo", keep=set(), keep_versions=0) == []
    assert (tmp_path / ".store" / "demo-1.0.0").is_dir()
