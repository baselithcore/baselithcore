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
from core.plugin_updates.release_manifest import file_digests, sign_release_manifest
from core.plugin_updates.sources import DEFAULT_MAX_ARTIFACT_BYTES, SourceError
from core.plugins import overlay
from core.plugins.integrity import compute_plugin_hash
from core.plugins.signing import generate_keypair_hex, sign_plugin_hash


def _signed_tarball(
    tmp: Path,
    priv: str,
    version: str = "1.2.0",
    *,
    docs: dict[str, str] | None = None,
    listed: dict[str, str] | None = None,
    extra_manifest: str = "",
) -> tuple[Path, dict]:
    """A signed release; ``docs`` are unhashed files, ``listed`` overrides ``files``."""
    src = tmp / "src" / "demo"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("X = 1\n")
    (src / "manifest.yaml").write_text(
        f"name: demo\nversion: {version}\nhash_surface_version: 5\n{extra_manifest}"
    )
    for rel, text in (docs or {}).items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text)
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
        "release_format": 2,
        "name": "demo",
        "version": version,
        "integrity_sha256": h,
        "signature_ed25519": sig,
        "tarball_sha256": hashlib.sha256(tgz.read_bytes()).hexdigest(),
        "files": listed if listed is not None else file_digests(src),
    }
    meta["manifest_signature_ed25519"] = sign_release_manifest(meta, priv)
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

    async def download(
        self, url: str, dest: Path, *, max_bytes: int | None = None
    ) -> None:
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
    meta["manifest_signature_ed25519"] = sign_release_manifest(meta, priv)
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

    monkeypatch.setattr("core.plugin_updates.archive.MAX_ARCHIVE_MEMBERS", 3)
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


async def test_legacy_release_is_shown_not_installable(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    for key in ("files", "manifest_signature_ed25519", "release_format"):
        meta.pop(key)
    src = _Source(
        _info(), {"u/tgz": tgz.read_bytes(), "u/json": json.dumps(meta).encode()}
    )
    cand = await _check(tmp_path, src, [pub])
    assert cand.refusal is Refusal.LEGACY_RELEASE and not cand.available
    assert cand.latest is not None and cand.latest.version == "1.2.0"
    assert src.downloads == 1  # release.json only; the tarball is never fetched


async def test_release_json_signed_by_a_stranger(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    stranger, _ = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    meta["manifest_signature_ed25519"] = sign_release_manifest(meta, stranger)
    src = _Source(
        _info(), {"u/tgz": tgz.read_bytes(), "u/json": json.dumps(meta).encode()}
    )
    cand = await _check(tmp_path, src, [pub])
    assert (
        cand.refusal is Refusal.SIGNATURE_INVALID
        and cand.detail == "release.json signature"
    )
    assert src.downloads == 1


async def test_extra_file_is_files_mismatch(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    _, clean = _signed_tarball(tmp_path / "clean", priv)
    tgz, meta = _signed_tarball(
        tmp_path / "dirty",
        priv,
        docs={"docs/extra.md": "not listed\n"},
        listed=clean["files"],
    )
    src = _Source(
        _info(), {"u/tgz": tgz.read_bytes(), "u/json": json.dumps(meta).encode()}
    )
    cand = await _check(tmp_path, src, [pub])
    assert (
        cand.refusal is Refusal.FILES_MISMATCH and cand.detail == "extra: docs/extra.md"
    )
    assert not UpdateCache(tmp_path / "c").tarball_path("demo", "1.2.0").exists()


async def test_changed_unhashed_file_is_files_mismatch(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    _, listed = _signed_tarball(tmp_path / "a", priv, docs={"docs/g.md": "original\n"})
    tgz, meta = _signed_tarball(
        tmp_path / "b", priv, docs={"docs/g.md": "tampered\n"}, listed=listed["files"]
    )
    src = _Source(
        _info(), {"u/tgz": tgz.read_bytes(), "u/json": json.dumps(meta).encode()}
    )
    cand = await _check(tmp_path, src, [pub])
    assert (
        cand.refusal is Refusal.FILES_MISMATCH and cand.detail == "changed: docs/g.md"
    )


async def test_unsafe_path_in_files_is_manifest_invalid(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv, listed={"../escape": "0" * 64})
    src = _Source(
        _info(), {"u/tgz": tgz.read_bytes(), "u/json": json.dumps(meta).encode()}
    )
    cand = await _check(tmp_path, src, [pub])
    assert cand.refusal is Refusal.MANIFEST_INVALID and "unsafe path" in cand.detail


def _served(tgz: Path, meta: dict | bytes) -> _Source:
    raw = meta if isinstance(meta, bytes) else json.dumps(meta).encode()
    return _Source(_info(), {"u/tgz": tgz.read_bytes(), "u/json": raw})


async def test_format_two_passes_and_signs_every_key(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv, docs={"docs/g.md": "x\n"})
    assert (await _check(tmp_path, _served(tgz, meta), [pub])).available
    for key, value in (("tarball_sha256", "0" * 64), ("python_dependencies", ["x"])):
        src = _served(tgz, {**meta, key: value})
        cand = await _check(tmp_path / key, src, [pub])
        assert cand.refusal is Refusal.SIGNATURE_INVALID and src.downloads == 1


async def test_no_trusted_keys_refuses_before_the_tarball(tmp_path: Path) -> None:
    priv, _ = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    src = _served(tgz, meta)
    cand = await _check(tmp_path, src, [])
    assert cand.refusal is Refusal.NO_TRUSTED_KEYS and src.downloads == 1


@pytest.mark.parametrize("fmt", [1, 3, "2", True, None])
async def test_unsupported_release_format(tmp_path: Path, fmt: object) -> None:
    priv, pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    meta["release_format"] = fmt
    meta["manifest_signature_ed25519"] = sign_release_manifest(meta, priv)
    src = _served(tgz, meta)
    cand = await _check(tmp_path, src, [pub])
    assert cand.refusal is Refusal.MANIFEST_INVALID and "release_format" in cand.detail
    assert src.downloads == 1


async def test_duplicate_keys_in_release_json(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    raw = json.dumps(meta).encode()
    raw = raw[:-1] + b', "version": "9.9.9"}'  # a second parser might read this one
    src = _served(tgz, raw)
    cand = await _check(tmp_path, src, [pub])
    assert cand.refusal is Refusal.MANIFEST_INVALID and "duplicate" in cand.detail
    assert src.downloads == 1


async def test_release_json_for_another_plugin(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    meta["name"] = "other"
    meta["manifest_signature_ed25519"] = sign_release_manifest(meta, priv)
    src = _served(tgz, meta)
    cand = await _check(tmp_path, src, [pub])
    assert (
        cand.refusal is Refusal.MANIFEST_INVALID
        and cand.detail == "release.json disagrees"
    )
    assert src.downloads == 1


async def test_listed_directory_is_files_mismatch(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    _, clean = _signed_tarball(tmp_path / "a", priv, docs={"docs/g.md": "x\n"})
    listed = {**clean["files"], "docs": "0" * 64}
    tgz, meta = _signed_tarball(
        tmp_path / "b", priv, docs={"docs/g.md": "x\n"}, listed=listed
    )
    cand = await _check(tmp_path, _served(tgz, meta), [pub])
    assert cand.refusal is Refusal.FILES_MISMATCH and cand.detail == "missing: docs"


async def test_hardlink_member_is_refused(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    good, meta = _signed_tarball(tmp_path, priv)
    evil = tmp_path / "evil.tgz"
    with tarfile.open(good) as src_tar, tarfile.open(evil, "w:gz") as tar:
        for member in src_tar.getmembers():
            tar.addfile(
                member, src_tar.extractfile(member) if member.isfile() else None
            )
        link = tarfile.TarInfo("demo/twin.py")
        link.type, link.linkname = tarfile.LNKTYPE, "demo/__init__.py"
        tar.addfile(link)
    meta["files"] = {**meta["files"], "twin.py": meta["files"]["__init__.py"]}
    meta["tarball_sha256"] = hashlib.sha256(evil.read_bytes()).hexdigest()
    meta["manifest_signature_ed25519"] = sign_release_manifest(meta, priv)
    cand = await _check(tmp_path, _served(evil, meta), [pub])
    assert cand.refusal is Refusal.MANIFEST_INVALID and "link or special" in cand.detail
    assert not UpdateCache(tmp_path / "c").tarball_path("demo", "1.2.0").exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_unpack_refuses_links_and_special_files(tmp_path: Path, kind: bytes) -> None:
    from core.plugin_updates import checker

    tgz = tmp_path / "l.tgz"
    with tarfile.open(tgz, "w:gz") as tar:
        tar.addfile(tarfile.TarInfo("p/a"), io.BytesIO(b""))
        info = tarfile.TarInfo("p/b")
        info.type, info.linkname = kind, "p/a"
        tar.addfile(info)
    with pytest.raises(tarfile.TarError, match="link or special"):
        checker._unpack(tgz, tmp_path / "out")
    assert not any((tmp_path / "out").iterdir())


@pytest.mark.parametrize(
    "names",
    [("p/a", "p/a"), ("p/a", "./p/a"), ("p/Readme", "p/README"), ("p/café", "p/café")],
)
def test_unpack_refuses_colliding_members(
    tmp_path: Path, names: tuple[str, str]
) -> None:
    from core.plugin_updates import checker

    tgz = tmp_path / "d.tgz"
    with tarfile.open(tgz, "w:gz") as tar:
        for name in names:
            tar.addfile(tarfile.TarInfo(name), io.BytesIO(b""))
    with pytest.raises(checker.UnsupportedMemberError, match="colliding"):
        checker._unpack(tgz, tmp_path / "out")


async def test_refusal_removes_a_cached_final_tarball(tmp_path: Path) -> None:
    priv, pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    cached = UpdateCache(tmp_path / "c").tarball_path("demo", "1.2.0")
    for legacy_key in ("files", "manifest_signature_ed25519", "release_format"):
        meta.pop(legacy_key)
    for served in (meta, b"not json", {**meta, "name": "other"}):
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(tgz.read_bytes())
        cand = await _check(tmp_path, _served(tgz, served), [pub])
        assert cand.refusal is not None and not cached.exists()
    cached.write_bytes(tgz.read_bytes())
    missing = _Source(_info(json_url=None), {})
    assert (await _check(tmp_path, missing, [pub])).refusal is Refusal.ARTIFACT_MISSING
    assert not cached.exists()


async def test_run_check_resolves_trust_roots_per_plugin(tmp_path: Path) -> None:
    """A callable ``trusted_keys`` is asked once per plugin, by name."""
    from core.plugin_updates.checker import run_check

    asked: list[str] = []

    def roots(plugin: str) -> list[str]:
        asked.append(plugin)
        return []

    report = await run_check(
        {"demo": "o/r", "other": "o/s"},
        {"demo": "1.1.0", "other": "1.0.0"},
        source=_Source(None, {}),  # type: ignore[arg-type]
        cache=UpdateCache(tmp_path / "c"),
        core_version="1.50.0",
        trusted_keys=roots,
    )
    assert sorted(asked) == ["demo", "other"]
    assert not any(c.available for c in report.candidates)
