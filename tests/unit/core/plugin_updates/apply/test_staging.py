from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from core.plugin_updates.apply.staging import StagingError, private_copy
from core.plugins.signing import generate_keypair_hex

from ..test_checker import _signed_tarball
from ._staging_fixtures import KNOWN, make_current, stage, store_is_clean


@pytest.fixture()
def keys() -> tuple[str, str]:
    return generate_keypair_hex()


def test_happy_path_promotes_and_carries_env(tmp_path: Path, keys) -> None:
    cur = make_current(tmp_path)
    staged = stage(tmp_path, keys, current=cur)
    assert staged.name == "demo-1.2.0"
    assert staged.path == tmp_path / "ov" / ".store" / "demo-1.2.0"
    env = staged.path / ".env"
    assert env.read_text() == "DEMO_TOKEN=s3cret\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert (tmp_path / "ov" / ".store" / "demo-1.2.0.release.json").is_file()
    assert set(staged.files) >= {"__init__.py"}
    assert store_is_clean(tmp_path)


def test_declared_state_is_carried(tmp_path: Path, keys) -> None:
    cur = make_current(
        tmp_path,
        extra={"var/db.sqlite": "rows"},
        manifest="runtime_state_paths: [var]\n",
    )
    staged = stage(tmp_path, keys, current=cur)
    assert (staged.path / "var" / "db.sqlite").read_text() == "rows"


def test_undeclared_state_refuses(tmp_path: Path, keys) -> None:
    cur = make_current(tmp_path, extra={"data/qdrant/seg.bin": "x"})
    with pytest.raises(StagingError) as err:
        stage(tmp_path, keys, current=cur)
    assert err.value.code == "undeclared_runtime_state"
    assert "data/qdrant/seg.bin" in err.value.detail
    assert not (tmp_path / "ov" / ".store" / "demo-1.2.0").exists()
    assert store_is_clean(tmp_path)


def test_replaced_cached_tarball_is_refused(tmp_path: Path, keys) -> None:
    priv, _ = keys
    tgz, meta = _signed_tarball(tmp_path, priv)
    pinned = meta["tarball_sha256"]
    other, _ = _signed_tarball(
        tmp_path / "again", priv, docs={"NOTES.md": "re-released"}
    )
    tgz.write_bytes(other.read_bytes())
    with pytest.raises(StagingError) as err:
        stage(
            tmp_path,
            keys,
            current=make_current(tmp_path),
            tgz=tgz,
            meta=meta,
            pinned=pinned,
        )
    assert err.value.code == "artifact_checksum"
    store = tmp_path / "ov" / ".store"
    assert not (store / "demo-1.2.0").exists() and store_is_clean(tmp_path)


def test_symlinked_tarball_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.tgz"
    real.write_bytes(b"x")
    link = tmp_path / "link.tgz"
    link.symlink_to(real)
    with pytest.raises(StagingError) as err:
        private_copy(link, tmp_path / "copy", "0" * 64)
    assert err.value.code == "artifact_checksum"
    assert not (tmp_path / "copy").exists()


def test_fifo_tarball_is_refused_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "fifo.tgz"
    os.mkfifo(fifo)
    with pytest.raises(StagingError) as err:
        private_copy(fifo, tmp_path / "copy", "0" * 64)
    assert err.value.code == "artifact_checksum"
    assert not (tmp_path / "copy").exists()


def test_private_copy_matches_and_mismatch_removes_dest(tmp_path: Path) -> None:
    import hashlib

    src = tmp_path / "a.tgz"
    src.write_bytes(b"payload")
    good = hashlib.sha256(b"payload").hexdigest()
    private_copy(src, tmp_path / "ok", good.upper())
    assert (tmp_path / "ok").read_bytes() == b"payload"
    with pytest.raises(StagingError):
        private_copy(src, tmp_path / "bad", "0" * 64)
    assert not (tmp_path / "bad").exists()


def test_not_newer_than_bundled_is_overlay_refused(tmp_path: Path, keys) -> None:
    cur = make_current(tmp_path)
    (cur / "manifest.yaml").write_text("name: demo\nversion: 1.3.0\n")
    with pytest.raises(StagingError) as err:
        stage(tmp_path, keys, current=cur)
    assert err.value.code in {"verification_failed", "overlay_refused"}
    assert store_is_clean(tmp_path)


def test_tampered_existing_store_entry_is_set_aside(tmp_path: Path, keys) -> None:
    bad = tmp_path / "ov" / ".store" / "demo-1.2.0"
    bad.mkdir(parents=True)
    (bad / "__init__.py").write_text("EVIL = 1\n")
    staged = stage(tmp_path, keys, current=make_current(tmp_path), known=KNOWN)
    assert (staged.path / "__init__.py").read_text() == "X = 1\n"
    assert list((tmp_path / "ov" / ".store").glob(".trash-*"))
