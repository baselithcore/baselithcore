from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from core.plugin_updates.cache import UpdateCache
from core.plugin_updates.checker import check_plugin, installed_versions
from core.plugin_updates.models import Refusal, ReleaseInfo
from core.plugin_updates.sources import DEFAULT_MAX_ARTIFACT_BYTES, SourceError
from core.plugins import overlay
from core.plugins.integrity import compute_plugin_hash
from core.plugins.signing import generate_keypair_hex, sign_plugin_hash


def _signed_tarball(tmp: Path, priv: str, version: str = "1.2.0") -> tuple[Path, dict]:
    src = tmp / "src" / "demo"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("X = 1\n")
    (src / "manifest.yaml").write_text(
        f"name: demo\nversion: {version}\nhash_surface_version: 5\n"
    )
    h = compute_plugin_hash(src)
    sig = sign_plugin_hash(h, priv)
    (src / "manifest.yaml").write_text(
        (src / "manifest.yaml").read_text()
        + f"integrity_sha256: {h}\nsignature_ed25519: {sig}\n"
    )
    tgz = tmp / f"demo-{version}.tar.gz"
    with tarfile.open(tgz, "w:gz") as tar:
        tar.add(src, arcname="demo")
    meta = {
        "name": "demo",
        "version": version,
        "integrity_sha256": h,
        "signature_ed25519": sig,
        "tarball_sha256": hashlib.sha256(tgz.read_bytes()).hexdigest(),
    }
    return tgz, meta


class _Source:
    def __init__(
        self, info: ReleaseInfo | None, files: dict[str, bytes], fail: bool = False
    ) -> None:
        self.info, self.files, self.fail, self.downloads = info, files, fail, 0
        self.max_bytes = DEFAULT_MAX_ARTIFACT_BYTES

    async def latest_release(self, plugin: str, slug: str) -> ReleaseInfo | None:
        if self.fail:
            raise SourceError("github 503")
        return self.info

    async def download(self, url: str, dest: Path) -> None:
        self.downloads += 1
        dest.write_bytes(self.files[url])


def _info(version: str = "1.2.0", json_url: str | None = "u/json") -> ReleaseInfo:
    return ReleaseInfo(
        plugin="demo",
        version=version,
        tag=f"v{version}",
        published_at=None,
        tarball_url="u/tgz",
        release_json_url=json_url,
    )


async def _check(
    tmp_path: Path, src: _Source, keys: list[str], installed: str = "1.1.0"
):
    return await check_plugin(
        "demo",
        "o/r",
        installed,
        source=src,  # type: ignore[arg-type]
        cache=UpdateCache(tmp_path / "c"),
        core_version="1.50.0",
        trusted_keys=keys,
    )


async def test_available_when_verified(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    src = _Source(
        _info(), {"u/tgz": tgz.read_bytes(), "u/json": json.dumps(meta).encode()}
    )
    cand = await _check(tmp_path, src, [pub])
    assert cand.available and cand.refusal is None
    assert UpdateCache(tmp_path / "c").tarball_path("demo", "1.2.0").exists()
    # A second check re-verifies the cached tarball instead of re-downloading it.
    before = src.downloads
    again = await _check(tmp_path, src, [pub])
    assert again.available and src.downloads == before + 1  # release.json only


async def test_checksum_mismatch(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    meta["tarball_sha256"] = "0" * 64
    src = _Source(
        _info(), {"u/tgz": tgz.read_bytes(), "u/json": json.dumps(meta).encode()}
    )
    cand = await _check(tmp_path, src, [pub])
    assert not cand.available and cand.refusal is Refusal.ARTIFACT_CHECKSUM
    assert not UpdateCache(tmp_path / "c").tarball_path("demo", "1.2.0").exists()


async def test_foreign_signature_not_available(tmp_path: Path) -> None:
    priv, _ = generate_keypair_hex()
    _, other_pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    src = _Source(
        _info(), {"u/tgz": tgz.read_bytes(), "u/json": json.dumps(meta).encode()}
    )
    cand = await _check(tmp_path, src, [other_pub])
    assert not cand.available and cand.refusal is Refusal.SIGNATURE_INVALID
    assert not UpdateCache(tmp_path / "c").tarball_path("demo", "1.2.0").exists()


async def test_up_to_date_downloads_nothing(tmp_path: Path) -> None:
    src = _Source(_info("1.1.0"), {})
    cand = await _check(tmp_path, src, ["x"])
    assert not cand.available and cand.refusal is None and src.downloads == 0


async def test_source_error_is_refusal(tmp_path: Path) -> None:
    cand = await _check(tmp_path, _Source(None, {}, fail=True), ["x"])
    assert cand.refusal is Refusal.SOURCE_ERROR and "503" in cand.detail


async def test_missing_release_json(tmp_path: Path) -> None:
    src = _Source(_info(json_url=None), {})
    cand = await _check(tmp_path, src, ["x"])
    assert cand.refusal is Refusal.ARTIFACT_MISSING and src.downloads == 0


async def test_release_json_disagrees(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    meta["version"] = "9.9.9"
    src = _Source(
        _info(), {"u/tgz": tgz.read_bytes(), "u/json": json.dumps(meta).encode()}
    )
    cand = await _check(tmp_path, src, [pub])
    assert cand.refusal is Refusal.MANIFEST_INVALID


async def test_unexpected_error_is_contained(tmp_path: Path) -> None:
    src = _Source(_info(), {"u/tgz": b"x", "u/json": b"not json token=SECRET"})
    cand = await _check(tmp_path, src, ["x"])
    assert cand.refusal is not None and not cand.available
    assert "SECRET" not in cand.detail


def test_tar_path_traversal_rejected(tmp_path: Path) -> None:
    from core.plugin_updates.checker import _unpack

    evil = tmp_path / "evil.tgz"
    with tarfile.open(evil, "w:gz") as tar:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 0
        tar.addfile(info)
    with pytest.raises(tarfile.TarError):
        _unpack(evil, tmp_path / "out")


def _tar_of(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_unpack_refuses_too_many_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.plugin_updates import checker

    monkeypatch.setattr(checker, "MAX_ARCHIVE_MEMBERS", 3)
    tgz = _tar_of(tmp_path / "m.tgz", {f"p/f{i}": b"" for i in range(4)})
    with pytest.raises(checker.ArchiveLimitError, match="more than 3 members"):
        checker._unpack(tgz, tmp_path / "out")
    assert not any((tmp_path / "out").iterdir())


def test_unpack_refuses_an_oversize_total(tmp_path: Path) -> None:
    from core.plugin_updates import checker

    tgz = _tar_of(tmp_path / "b.tgz", {"p/a": b"x" * 60, "p/b": b"x" * 60})
    with pytest.raises(checker.ArchiveLimitError, match="more than 100 bytes"):
        checker._unpack(tgz, tmp_path / "out", max_bytes=100)
    assert not any((tmp_path / "out").iterdir())
    checker._unpack(tgz, tmp_path / "ok", max_bytes=120)
    assert (tmp_path / "ok" / "p" / "b").stat().st_size == 60


async def test_oversize_unpack_is_a_refusal(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    src = _Source(
        _info(), {"u/tgz": tgz.read_bytes(), "u/json": json.dumps(meta).encode()}
    )
    src.max_bytes = 16  # unpacked cap 64 bytes: smaller than the plugin
    cand = await _check(tmp_path, src, [pub])
    assert not cand.available and cand.refusal is Refusal.MANIFEST_INVALID
    assert cand.detail.startswith("archive too large")


def test_installed_versions_prefer_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / "plugins" / "demo"
    bundled.mkdir(parents=True)
    (bundled / "__init__.py").write_text("")
    (bundled / "manifest.yaml").write_text("name: demo\nversion: 1.0.0\n")
    ov = tmp_path / "ov" / "demo"
    ov.mkdir(parents=True)
    (ov / "__init__.py").write_text("")
    (ov / "manifest.yaml").write_text("name: demo\nversion: 1.1.0\n")
    monkeypatch.setattr(overlay, "_REGISTERED", {"demo": ov})
    assert installed_versions(tmp_path / "plugins")["demo"] == "1.1.0"
