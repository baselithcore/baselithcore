"""Adversarial staging: escapes, limits, links, live entries, cleanup."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from core.plugin_updates.apply import staging as staging_mod
from core.plugin_updates.apply.staging import StagingError, known_files_of
from core.plugin_updates.release_manifest import sign_release_manifest
from core.plugins.signing import generate_keypair_hex

from ..test_checker import _signed_tarball
from ._staging_fixtures import make_current, stage, store_is_clean


@pytest.fixture()
def keys() -> tuple[str, str]:
    return generate_keypair_hex()


def _repin(meta: dict, tgz: Path, priv: str) -> str:
    """Sign ``meta`` for ``tgz`` so only the tarball's content is at fault."""
    digest = hashlib.sha256(tgz.read_bytes()).hexdigest()
    meta["tarball_sha256"] = digest
    meta["manifest_signature_ed25519"] = sign_release_manifest(meta, priv)
    return digest


def _raw_tarball(
    path: Path, members: dict[str, bytes], meta: dict, priv: str
) -> tuple[Path, str]:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path, _repin(meta, path, priv)


def test_path_escape_is_refused_and_nothing_lands_outside(tmp_path: Path, keys) -> None:
    _, meta = _signed_tarball(tmp_path, keys[0])
    tgz, digest = _raw_tarball(
        tmp_path / "evil.tgz", {"../../escaped.txt": b"x"}, meta, keys[0]
    )
    with pytest.raises(StagingError) as err:
        stage(tmp_path, keys, current=None, tgz=tgz, meta=meta, pinned=digest)
    assert err.value.code == "verification_failed"
    assert not list(tmp_path.rglob("escaped.txt"))
    assert store_is_clean(tmp_path)


def test_unpack_is_bounded(tmp_path: Path, keys) -> None:
    with pytest.raises(StagingError) as err:
        stage(tmp_path, keys, current=None, max_bytes=3)
    assert err.value.code == "verification_failed"
    assert store_is_clean(tmp_path)


def test_two_roots_are_refused(tmp_path: Path, keys) -> None:
    _, meta = _signed_tarball(tmp_path, keys[0])
    tgz, digest = _raw_tarball(
        tmp_path / "two.tgz", {"demo/a.py": b"", "other/b.py": b""}, meta, keys[0]
    )
    with pytest.raises(StagingError) as err:
        stage(tmp_path, keys, current=None, tgz=tgz, meta=meta, pinned=digest)
    assert err.value.code == "verification_failed"


def test_unsigned_meta_is_refused(tmp_path: Path, keys) -> None:
    tgz, meta = _signed_tarball(tmp_path, keys[0])
    meta["files"] = {**meta["files"], "extra.txt": "0" * 64}  # not re-signed
    with pytest.raises(StagingError) as err:
        stage(tmp_path, keys, current=None, tgz=tgz, meta=meta)
    assert err.value.code == "verification_failed"


def test_meta_must_pin_the_same_tarball(tmp_path: Path, keys) -> None:
    tgz, meta = _signed_tarball(tmp_path, keys[0])
    with pytest.raises(StagingError) as err:
        stage(tmp_path, keys, current=None, tgz=tgz, meta=meta, pinned="a" * 64)
    assert err.value.code == "artifact_checksum"


def test_fresh_install_without_current_tree(tmp_path: Path, keys) -> None:
    staged = stage(tmp_path, keys, current=None, installed=None)
    assert (staged.path / "__init__.py").read_text() == "X = 1\n"
    assert not (staged.path / ".env").exists()


def test_carry_never_overwrites_a_shipped_file(tmp_path: Path, keys) -> None:
    tgz, meta = _signed_tarball(tmp_path, keys[0], docs={"var/README.md": "shipped\n"})
    cur = make_current(
        tmp_path,
        extra={"var/README.md": "old\n", "var/db.sqlite": "rows"},
        manifest="runtime_state_paths: [var]\n",
    )
    staged = stage(tmp_path, keys, current=cur, tgz=tgz, meta=meta)
    assert (staged.path / "var" / "README.md").read_text() == "shipped\n"
    assert (staged.path / "var" / "db.sqlite").read_text() == "rows"


def test_carried_code_fails_reverification(tmp_path: Path, keys) -> None:
    cur = make_current(
        tmp_path,
        extra={"var/evil.py": "EVIL = 1\n"},
        manifest="runtime_state_paths: [var]\n",
    )
    with pytest.raises(StagingError) as err:
        stage(tmp_path, keys, current=cur)
    assert err.value.code == "overlay_refused"
    assert not (tmp_path / "ov" / ".store" / "demo-1.2.0").exists()
    assert store_is_clean(tmp_path)


def test_declared_path_through_a_symlink_is_refused(tmp_path: Path, keys) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("do not copy")
    cur = make_current(tmp_path, manifest="runtime_state_paths: [var/db]\n")
    (cur / "var").symlink_to(outside)
    with pytest.raises(StagingError) as err:
        stage(
            tmp_path, keys, current=cur, known={"manifest.yaml", "__init__.py", "var"}
        )
    assert err.value.code == "overlay_refused"
    assert not list((tmp_path / "ov").rglob("secret"))
    assert store_is_clean(tmp_path)


def test_symlinked_env_is_refused_not_followed(tmp_path: Path, keys) -> None:
    cur = make_current(tmp_path)
    (cur / ".env").unlink()
    target = tmp_path / "shadow"
    target.write_text("root:x\n")
    (cur / ".env").symlink_to(target)
    with pytest.raises(StagingError) as err:
        stage(tmp_path, keys, current=cur)
    assert err.value.code == "overlay_refused"
    assert not list((tmp_path / "ov").rglob(".env"))


def test_live_entry_is_never_set_aside(tmp_path: Path, keys) -> None:
    live = tmp_path / "ov" / ".store" / "demo-1.2.0"
    live.mkdir(parents=True)
    (live / "__init__.py").write_text("LIVE = 1\n")
    os.symlink(os.path.join(".store", "demo-1.2.0"), tmp_path / "ov" / "demo")
    with pytest.raises(StagingError) as err:
        stage(tmp_path, keys, current=make_current(tmp_path))
    assert err.value.code == "overlay_refused"
    assert (live / "__init__.py").read_text() == "LIVE = 1\n"
    assert store_is_clean(tmp_path)


def test_symlinked_store_is_refused(tmp_path: Path, keys) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "ov").mkdir()
    (tmp_path / "ov" / ".store").symlink_to(elsewhere)
    with pytest.raises(StagingError) as err:
        stage(tmp_path, keys, current=None)
    assert err.value.code == "overlay_refused"
    assert not list(elsewhere.iterdir())


def test_staging_is_cleaned_on_unexpected_error(
    tmp_path: Path, keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(staging_mod, "verify_release", boom)
    with pytest.raises(RuntimeError):
        stage(tmp_path, keys, current=None)
    assert store_is_clean(tmp_path)


def test_read_only_tree_is_still_cleaned(tmp_path: Path, keys) -> None:
    _, meta = _signed_tarball(tmp_path, keys[0])
    tgz = tmp_path / "ro.tgz"
    with tarfile.open(tgz, "w:gz") as tar:
        d = tarfile.TarInfo("demo/locked")
        d.type, d.mode = tarfile.DIRTYPE, 0o500
        tar.addfile(d)
        f = tarfile.TarInfo("demo/locked/f.txt")
        tar.addfile(f, io.BytesIO(b""))
    digest = _repin(meta, tgz, keys[0])
    with pytest.raises(StagingError):
        stage(tmp_path, keys, current=None, tgz=tgz, meta=meta, pinned=digest)
    assert store_is_clean(tmp_path)


def test_sidecar_is_the_release_meta(tmp_path: Path, keys) -> None:
    staged = stage(tmp_path, keys, current=None)
    sidecar = tmp_path / "ov" / ".store" / "demo-1.2.0.release.json"
    assert json.loads(sidecar.read_text())["files"] == staged.files


def test_known_files_from_store_sidecar(tmp_path: Path, keys) -> None:
    staged = stage(tmp_path, keys, current=None)
    os.symlink(os.path.join(".store", staged.name), tmp_path / "ov" / "demo")
    got = known_files_of(tmp_path / "ov" / "demo", overlay_root=tmp_path / "ov")
    assert got == set(staged.files)


def test_known_files_from_git_then_walk(tmp_path: Path) -> None:
    cur = make_current(tmp_path)
    got = known_files_of(cur, overlay_root=None, git_ls_files=lambda _p: ["a.py"])
    assert got == {"a.py"}

    def no_git(_p: Path) -> list[str]:
        raise OSError("no git")

    walked = known_files_of(cur, overlay_root=tmp_path / "ov", git_ls_files=no_git)
    assert {"manifest.yaml", "__init__.py"} <= walked


def test_remove_clears_a_read_only_tree(tmp_path: Path) -> None:
    locked = tmp_path / "scratch" / "locked"
    locked.mkdir(parents=True)
    (locked / "f.txt").write_text("x")
    os.chmod(locked, 0o500)
    staging_mod._remove(tmp_path / "scratch")
    assert not (tmp_path / "scratch").exists()


def test_store_entry_without_release_record_fails_closed(tmp_path: Path) -> None:
    entry = tmp_path / "ov" / ".store" / "demo-1.2.0"
    entry.mkdir(parents=True)
    with pytest.raises(StagingError) as err:
        known_files_of(entry, overlay_root=tmp_path / "ov")
    assert err.value.code == "undeclared_runtime_state"


def test_link_inside_carried_dir_is_refused(tmp_path: Path, keys) -> None:
    (tmp_path / "secret").write_text("nope")
    cur = make_current(
        tmp_path,
        extra={"var/db.sqlite": "rows"},
        manifest="runtime_state_paths: [var]\n",
    )
    (cur / "var" / "leak").symlink_to(tmp_path / "secret")
    with pytest.raises(StagingError) as err:
        stage(tmp_path, keys, current=cur, known={"manifest.yaml", "__init__.py"})
    assert err.value.code == "overlay_refused" and "var/leak" in err.value.detail


@pytest.mark.parametrize(
    "run_id",
    ["../escape", "pinstall-bad", "pinstall-20260930T120000Z-0000abcd\n", "x"],
)
def test_run_id_must_match_the_store_format(tmp_path: Path, keys, run_id: str) -> None:
    from core.plugin_updates.apply.staging import stage_release

    tgz, meta = _signed_tarball(tmp_path, keys[0])
    with pytest.raises(StagingError) as err:
        stage_release(
            plugin="demo",
            version="1.2.0",
            tarball=tgz,
            meta=meta,
            pinned_sha256=meta["tarball_sha256"],
            overlay_root=tmp_path / "ov",
            current_dir=None,
            known_files=(),
            installed_version=None,
            bundled_root=None,
            core_version="1.50.0",
            trusted_keys=[keys[1]],
            run_id=run_id,
            max_unpacked_bytes=10_000_000,
        )
    assert err.value.code == "overlay_refused"
    assert not list((tmp_path / "ov" / ".store").glob(".staging-*"))


def test_failed_promote_leaves_no_sidecar(
    tmp_path: Path, keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = os.rename

    def fail_on_promote(src, dst, *a, **k):  # type: ignore[no-untyped-def]
        if Path(dst).name == "demo-1.2.0" and ".staging-" in str(src):
            raise OSError("disk full")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "rename", fail_on_promote)
    with pytest.raises(OSError):
        stage(tmp_path, keys, current=None)
    store = tmp_path / "ov" / ".store"
    assert not (store / "demo-1.2.0.release.json").exists()
    assert not (store / "demo-1.2.0").exists() and store_is_clean(tmp_path)


def test_old_sidecar_goes_to_trash_with_its_entry(tmp_path: Path, keys) -> None:
    store = tmp_path / "ov" / ".store"
    (store / "demo-1.2.0").mkdir(parents=True)
    (store / "demo-1.2.0.release.json").write_text('{"old": true}')
    stage(tmp_path, keys, current=None)
    trashed = list(store.glob(".trash-*/demo-1.2.0.release.json"))
    assert len(trashed) == 1 and "old" in trashed[0].read_text()
    assert "old" not in (store / "demo-1.2.0.release.json").read_text()


def test_entry_appearing_before_rename_is_refused(
    tmp_path: Path, keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = staging_mod._set_aside

    def racing(*args):  # type: ignore[no-untyped-def]
        real(*args)
        (tmp_path / "ov" / ".store" / "demo-1.2.0").mkdir()

    (tmp_path / "ov" / ".store" / "demo-1.2.0").mkdir(parents=True)
    monkeypatch.setattr(staging_mod, "_set_aside", racing)
    with pytest.raises(StagingError) as err:
        stage(tmp_path, keys, current=None)
    assert err.value.code == "overlay_refused"
    assert not any((tmp_path / "ov" / ".store" / "demo-1.2.0").iterdir())
    assert not (tmp_path / "ov" / ".store" / "demo-1.2.0.release.json").exists()


def test_malformed_installed_manifest_is_verification_failed(
    tmp_path: Path, keys
) -> None:
    cur = make_current(tmp_path, manifest="runtime_state_paths: [/abs]\n")
    with pytest.raises(StagingError) as err:
        stage(tmp_path, keys, current=cur)
    assert err.value.code == "verification_failed"


def test_details_name_paths_relative_to_the_overlay(tmp_path: Path, keys) -> None:
    store = tmp_path / "ov" / ".store"
    (store / "demo-1.2.0").mkdir(parents=True)
    (store / "other-1.0.0").mkdir()
    os.symlink(os.path.join(".store", "other-1.0.0"), tmp_path / "ov" / "demo")
    with pytest.raises(StagingError) as err:
        stage(tmp_path, keys, current=None)
    assert err.value.code == "overlay_refused"
    assert str(tmp_path) not in err.value.detail
    assert str(tmp_path.resolve()) not in err.value.detail


def test_relative_details() -> None:
    ov = Path("/srv/ov")
    got = staging_mod._relative(
        "/srv/ov/demo x; /srv/bundled/demo/var y", ov, Path("/srv/bundled/demo")
    )
    assert got == "demo x; <installed>/var y"
