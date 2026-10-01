"""Download, pin and verify one release's signed assets; keep the tarball."""

from __future__ import annotations

import asyncio
import os
import shutil
import tarfile
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from .archive import (
    MAX_ARCHIVE_MEMBERS,
    UNPACKED_SIZE_FACTOR,
    sha256_file,
    verify_tarball,
)
from .cache import UpdateCache
from .models import Refusal, ReleaseInfo, SignedAssets
from .release_manifest import (
    RELEASE_FORMAT,
    ReleaseManifestError,
    is_legacy,
    load_release_json,
    parse_files,
    verify_release_manifest,
)
from .sources import GitHubReleaseSource, SourceError


def _refused(refusal: Refusal, detail: str = "") -> SignedAssets:
    return SignedAssets(verified=False, refusal=refusal, detail=detail)


def manifest_refusal(
    meta: dict[str, Any], trusted_keys: Sequence[str]
) -> tuple[Refusal, str] | None:
    """Refuse a legacy, unsigned, foreign-signed or malformed ``release.json``."""
    if is_legacy(meta):
        return Refusal.LEGACY_RELEASE, "release.json has no signed file list"
    if not trusted_keys:
        return Refusal.NO_TRUSTED_KEYS, ""
    if not verify_release_manifest(meta, trusted_keys):
        return Refusal.SIGNATURE_INVALID, "release.json signature"
    fmt = meta.get("release_format")
    if type(fmt) is not int or fmt != RELEASE_FORMAT:
        return Refusal.MANIFEST_INVALID, "unsupported release_format"
    try:
        parse_files(meta, max_entries=MAX_ARCHIVE_MEMBERS)
    except ReleaseManifestError as exc:
        return Refusal.MANIFEST_INVALID, str(exc)
    return None


def _host_build_required(tarball: Path, plugin: str) -> bool:
    """The verified manifest's ``host_build_required`` flag (read pre-publish)."""
    with tarfile.open(tarball, "r:gz") as tar:
        for name in ("manifest.yaml", "manifest.yml", "manifest.json"):
            try:
                member = tar.getmember(f"{plugin}/{name}")
            except KeyError:
                continue
            handle = tar.extractfile(member)
            data = yaml.safe_load(handle.read()) if handle else None
            return bool(
                isinstance(data, dict) and data.get("host_build_required") is True
            )
    return False


async def verify_signed_assets(
    plugin: str,
    latest: ReleaseInfo,
    installed: str | None,
    *,
    source: GitHubReleaseSource,
    cache: UpdateCache,
    core_version: str,
    trusted_keys: Sequence[str],
) -> SignedAssets:
    """Verify ``latest``'s signed assets; keep its tarball only when they pass.

    Never raises :class:`SourceError` (it becomes ``SOURCE_ERROR``). On success
    the tarball is at ``cache.tarball_path(plugin, latest.version)``; on any
    refusal that path is removed.
    """
    if not latest.tarball_url or not latest.release_json_url:
        return _refused(Refusal.ARTIFACT_MISSING, "no signed assets attached")
    final = cache.tarball_path(plugin, latest.version)
    try:
        result = await _verify(
            plugin, latest, installed, final, source, core_version, trusted_keys
        )
    except SourceError as exc:
        result = _refused(Refusal.SOURCE_ERROR, str(exc))
    except Exception:
        final.unlink(missing_ok=True)
        raise
    if not result.verified:
        final.unlink(missing_ok=True)
    return result


async def _verify(
    plugin: str,
    latest: ReleaseInfo,
    installed: str | None,
    final: Path,
    source: GitHubReleaseSource,
    core_version: str,
    trusted_keys: Sequence[str],
) -> SignedAssets:
    assert latest.tarball_url and latest.release_json_url
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=".dl-", dir=final.parent))
    try:
        meta_path = tmp_dir / "release.json"
        await source.download(latest.release_json_url, meta_path)
        try:
            meta = load_release_json(meta_path.read_bytes())
        except ReleaseManifestError as exc:
            return _refused(Refusal.MANIFEST_INVALID, str(exc))
        if meta.get("name") != plugin or str(meta.get("version")) != latest.version:
            return _refused(Refusal.MANIFEST_INVALID, "release.json disagrees")
        refused = manifest_refusal(meta, trusted_keys)
        if refused is not None:
            return _refused(*refused)
        files = parse_files(meta, max_entries=MAX_ARCHIVE_MEMBERS)
        cached = await asyncio.to_thread(final.is_file)
        tgz = final if cached else tmp_dir / "release.tar.gz"
        if tgz is not final:
            await source.download(latest.tarball_url, tgz)
        pinned = str(meta.get("tarball_sha256", "")).lower()
        if sha256_file(tgz) != pinned:
            return _refused(Refusal.ARTIFACT_CHECKSUM)
        result = await asyncio.to_thread(
            verify_tarball,
            tgz,
            plugin,
            latest.version,
            installed,
            core_version,
            trusted_keys,
            source.max_bytes * UNPACKED_SIZE_FACTOR,
            files,
        )
        if not result.ok:
            return _refused(result.refusal or Refusal.MANIFEST_INVALID, result.detail)
        # Read the flag before publishing the tarball: a failure here must not
        # leave a verified-looking tarball in the cache.
        host_build = await asyncio.to_thread(_host_build_required, tgz, plugin)
        if tgz is not final:
            os.replace(tgz, final)
        return SignedAssets(
            verified=True,
            tarball_sha256=pinned,
            files_count=len(files),
            host_build_required=host_build,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


__all__ = ["manifest_refusal", "verify_signed_assets"]
