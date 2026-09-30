"""Shared fixtures for the staging tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core.plugin_updates.apply.staging import StagedRelease, stage_release

from ..test_checker import _signed_tarball

RUN_ID = "pinstall-20260930T120000Z-0000abcd"
KNOWN = {"manifest.yaml", "__init__.py"}


def make_current(
    tmp_path: Path, *, extra: dict[str, str] | None = None, manifest: str = ""
) -> Path:
    cur = tmp_path / "bundled" / "demo"
    cur.mkdir(parents=True)
    (cur / "manifest.yaml").write_text(f"name: demo\nversion: 1.1.0\n{manifest}")
    (cur / "__init__.py").write_text("X = 0\n")
    (cur / ".env").write_text("DEMO_TOKEN=s3cret\n")
    os.chmod(cur / ".env", 0o600)
    for rel, text in (extra or {}).items():
        (cur / rel).parent.mkdir(parents=True, exist_ok=True)
        (cur / rel).write_text(text)
    return cur


def stage(
    tmp_path: Path,
    keys: tuple[str, str],
    *,
    current: Path | None,
    known: set[str] = KNOWN,
    tgz: Path | None = None,
    meta: dict[str, Any] | None = None,
    pinned: str | None = None,
    installed: str | None = "1.1.0",
    max_bytes: int = 10_000_000,
) -> StagedRelease:
    priv, pub = keys
    if tgz is None:
        tgz, meta = _signed_tarball(tmp_path, priv)
    assert meta is not None
    return stage_release(
        plugin="demo",
        version="1.2.0",
        tarball=tgz,
        meta=meta,
        pinned_sha256=pinned or meta["tarball_sha256"],
        overlay_root=tmp_path / "ov",
        current_dir=current,
        known_files=known,
        installed_version=installed,
        bundled_root=tmp_path / "bundled",
        core_version="1.50.0",
        trusted_keys=[pub],
        run_id=RUN_ID,
        max_unpacked_bytes=max_bytes,
    )


def store_is_clean(tmp_path: Path) -> bool:
    store = tmp_path / "ov" / ".store"
    return not list(store.glob(".staging-*"))


__all__ = ["KNOWN", "RUN_ID", "make_current", "stage", "store_is_clean"]
