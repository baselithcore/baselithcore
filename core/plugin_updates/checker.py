"""Check plugins for newer signed releases and verify them before offering."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import posixpath
import shutil
import tarfile
import tempfile
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from core.plugins.discovery import apply_overlay
from core.plugins.integrity import find_manifest_file
from core.plugins.overlay import registered_overlay_dirs
from core.plugins.version import SemanticVersion

from .cache import UpdateCache
from .models import (
    CheckReport,
    Refusal,
    ReleaseInfo,
    UpdateCandidate,
    VerificationResult,
)
from .release_manifest import (
    RELEASE_FORMAT,
    ReleaseManifestError,
    is_legacy,
    load_release_json,
    parse_files,
    verify_release_manifest,
)
from .sources import GitHubReleaseSource, SourceError
from .verifier import verify_release

logger = logging.getLogger(__name__)

_CONCURRENCY = 4
#: Members an update tarball may hold, and how many times the download cap its
#: unpacked size may reach: the archive is opened before its signature is
#: checked, so neither may be left to the publisher.
MAX_ARCHIVE_MEMBERS = 20_000
UNPACKED_SIZE_FACTOR = 4


class ArchiveLimitError(tarfile.TarError):
    """An update tarball holds too many members or unpacks too large."""


class UnsupportedMemberError(tarfile.TarError):
    """An update tarball holds a link, device or other non-regular member."""


def _manifest_version(plugin_dir: Path) -> str | None:
    manifest = find_manifest_file(plugin_dir)
    if manifest is None:
        return None
    try:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("version") is None:
        return None
    return str(data["version"])


def installed_versions(bundled_root: Path) -> dict[str, str]:
    """Manifest ``version`` per plugin, preferring the overlay entry."""
    scanned = (
        sorted(p for p in bundled_root.iterdir() if p.is_dir())
        if bundled_root.is_dir()
        else []
    )
    versions: dict[str, str] = {}
    for plugin_dir in apply_overlay(scanned, registered_overlay_dirs()):
        version = _manifest_version(plugin_dir)
        if version is not None:
            versions[plugin_dir.name] = version
    return versions


def _unpack(tarball: Path, dest: Path, max_bytes: int | None = None) -> None:
    """Extract ``tarball`` into ``dest``; unsafe members raise ``TarError``.

    Members are counted and sized from their headers before anything is
    written: more than :data:`MAX_ARCHIVE_MEMBERS`, or a declared total above
    ``max_bytes``, raises :class:`ArchiveLimitError`; any member that is not a
    regular file or a directory (a symbolic or hard link, a device, a FIFO),
    or whose path repeats another's up to case and Unicode normalisation,
    raises :class:`UnsupportedMemberError` — which of two such members wins
    depends on the filesystem, so the verified tree could differ from the one
    installed later.
    """
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as tar:
        total = 0
        seen: set[str] = set()
        for count, member in enumerate(tar, start=1):
            if count > MAX_ARCHIVE_MEMBERS:
                raise ArchiveLimitError(f"more than {MAX_ARCHIVE_MEMBERS} members")
            if not (member.isfile() or member.isdir()):
                raise UnsupportedMemberError("a link or special file")
            folded = unicodedata.normalize(
                "NFC", posixpath.normpath(member.name)
            ).casefold()
            if folded in seen:
                raise UnsupportedMemberError("a duplicate or case-colliding member")
            seen.add(folded)
            total += max(member.size, 0)
            if max_bytes is not None and total > max_bytes:
                raise ArchiveLimitError(f"unpacks to more than {max_bytes} bytes")
        tar.extractall(dest, filter="data")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_newer(latest: str, installed: str | None) -> bool:
    if installed is None:
        return True
    try:
        return SemanticVersion(latest) > SemanticVersion(installed)
    except ValueError:
        return True


def _refused(
    plugin: str,
    installed: str | None,
    latest: ReleaseInfo | None,
    refusal: Refusal,
    detail: str = "",
) -> UpdateCandidate:
    return UpdateCandidate(
        plugin=plugin,
        installed_version=installed,
        latest=latest,
        available=False,
        refusal=refusal,
        detail=detail,
    )


def _verify_tarball(
    tarball: Path,
    plugin: str,
    version: str,
    installed: str | None,
    core_version: str,
    trusted_keys: Sequence[str],
    max_unpacked_bytes: int | None = None,
    expected_files: Mapping[str, str] | None = None,
    requirement_ok: Callable[[str], bool] | None = None,
) -> VerificationResult:
    extra: dict[str, Any] = (
        {} if requirement_ok is None else {"requirement_ok": requirement_ok}
    )
    with tempfile.TemporaryDirectory(prefix="plugin-update-") as tmp:
        root = Path(tmp) / "unpacked"
        try:
            _unpack(tarball, root, max_unpacked_bytes)
        except ArchiveLimitError as exc:
            return _bad_archive(f"archive too large: {exc}")
        except UnsupportedMemberError as exc:
            return _bad_archive(f"archive holds {exc}")
        except (tarfile.TarError, OSError, EOFError) as exc:
            return _bad_archive(f"unpack failed: {type(exc).__name__}")
        entries = list(root.iterdir())
        if len(entries) != 1 or not entries[0].is_dir() or entries[0].is_symlink():
            return _bad_archive("tarball must hold a single plugin directory")
        return verify_release(
            entries[0],
            expected_name=plugin,
            expected_version=version,
            installed_version=installed,
            core_version=core_version,
            trusted_keys=trusted_keys,
            expected_files=expected_files,
            **extra,
        )


def verify_release_tarball(
    tarball: Path,
    plugin: str,
    version: str,
    core_version: str,
    trusted_keys: Sequence[str],
    *,
    expected_files: Mapping[str, str] | None = None,
    requirement_ok: Callable[[str], bool] | None = None,
) -> VerificationResult:
    """Unpack and verify a release tarball exactly as a deployment does.

    Shares the unpack (member types, case/NFC collisions, limits), the
    single-root rule and :func:`verify_release` with the update checker.
    ``requirement_ok`` overrides the dependency predicate (release-time
    callers must not judge the publisher's environment).
    """
    return _verify_tarball(
        tarball,
        plugin,
        version,
        None,
        core_version,
        trusted_keys,
        expected_files=expected_files,
        requirement_ok=requirement_ok,
    )


def _bad_archive(detail: str) -> VerificationResult:
    return VerificationResult(refusal=Refusal.MANIFEST_INVALID, detail=detail)


def _release_manifest_refusal(
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


async def _check(
    plugin: str,
    slug: str,
    installed: str | None,
    source: GitHubReleaseSource,
    cache: UpdateCache,
    core_version: str,
    trusted_keys: Sequence[str],
) -> UpdateCandidate:
    """:func:`_check_release`, dropping the cached tarball of any refused release.

    Every refusal path — not only one that downloaded — removes it, so a
    tarball verified under earlier rules never outlives a refusal of its
    release under the current ones.
    """
    candidate = await _check_release(
        plugin, slug, installed, source, cache, core_version, trusted_keys
    )
    if candidate.refusal is not None and candidate.latest is not None:
        cache.tarball_path(plugin, candidate.latest.version).unlink(missing_ok=True)
    return candidate


async def _check_release(
    plugin: str,
    slug: str,
    installed: str | None,
    source: GitHubReleaseSource,
    cache: UpdateCache,
    core_version: str,
    trusted_keys: Sequence[str],
) -> UpdateCandidate:
    latest = await source.latest_release(plugin, slug)
    if latest is None:
        return UpdateCandidate(
            plugin=plugin, installed_version=installed, latest=None, available=False
        )
    if not latest.tarball_url or not latest.release_json_url:
        return _refused(plugin, installed, latest, Refusal.ARTIFACT_MISSING)
    if not _is_newer(latest.version, installed):
        return UpdateCandidate(
            plugin=plugin, installed_version=installed, latest=latest, available=False
        )

    final = cache.tarball_path(plugin, latest.version)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=".dl-", dir=final.parent))
    try:
        meta_path = tmp_dir / "release.json"
        await source.download(latest.release_json_url, meta_path)
        try:
            meta = load_release_json(meta_path.read_bytes())
        except ReleaseManifestError as exc:
            return _refused(
                plugin, installed, latest, Refusal.MANIFEST_INVALID, str(exc)
            )
        if meta.get("name") != plugin or str(meta.get("version")) != latest.version:
            return _refused(
                plugin,
                installed,
                latest,
                Refusal.MANIFEST_INVALID,
                "release.json disagrees",
            )
        manifest_refusal = _release_manifest_refusal(meta, trusted_keys)
        if manifest_refusal is not None:
            return _refused(plugin, installed, latest, *manifest_refusal)
        files = parse_files(meta, max_entries=MAX_ARCHIVE_MEMBERS)
        candidate_tgz = final if final.is_file() else tmp_dir / "release.tar.gz"
        if candidate_tgz is not final:
            await source.download(latest.tarball_url, candidate_tgz)
        if _sha256(candidate_tgz) != str(meta.get("tarball_sha256", "")).lower():
            final.unlink(missing_ok=True)
            return _refused(plugin, installed, latest, Refusal.ARTIFACT_CHECKSUM)
        result = await asyncio.to_thread(
            _verify_tarball,
            candidate_tgz,
            plugin,
            latest.version,
            installed,
            core_version,
            trusted_keys,
            source.max_bytes * UNPACKED_SIZE_FACTOR,
            files,
        )
        if not result.ok:
            final.unlink(missing_ok=True)
            return _refused(
                plugin,
                installed,
                latest,
                result.refusal or Refusal.MANIFEST_INVALID,
                result.detail,
            )
        if candidate_tgz is not final:
            os.replace(candidate_tgz, final)
        return UpdateCandidate(
            plugin=plugin, installed_version=installed, latest=latest, available=True
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def check_plugin(
    plugin: str,
    slug: str,
    installed: str | None,
    *,
    source: GitHubReleaseSource,
    cache: UpdateCache,
    core_version: str,
    trusted_keys: Sequence[str],
) -> UpdateCandidate:
    """Check one plugin; never raises (failures become a refusal).

    Only a release whose ``release.json`` is format 2 and signed by a trusted
    key (checked before the tarball is fetched), whose tarball matches the
    signed checksum, and whose unpacked files match the signed ``files`` list
    and pass :func:`verify_release` is reported ``available``; its tarball is
    then kept in ``cache`` and any refused one is removed.
    """
    try:
        return await _check(
            plugin, slug, installed, source, cache, core_version, trusted_keys
        )
    except SourceError as exc:
        return _refused(plugin, installed, None, Refusal.SOURCE_ERROR, str(exc))
    except Exception as exc:  # contain everything; the type name carries no secret
        logger.warning("plugin_update_check_failed: %s: %s", plugin, type(exc).__name__)
        return _refused(
            plugin,
            installed,
            None,
            Refusal.SOURCE_ERROR,
            f"unexpected {type(exc).__name__}",
        )


async def run_check(
    sources: dict[str, str],
    installed: dict[str, str],
    *,
    source: GitHubReleaseSource,
    cache: UpdateCache,
    core_version: str,
    trusted_keys: Sequence[str],
) -> CheckReport:
    """Check every plugin that has both a source and an installed version."""
    gate = asyncio.Semaphore(_CONCURRENCY)

    async def one(name: str) -> UpdateCandidate:
        async with gate:
            return await check_plugin(
                name,
                sources[name],
                installed[name],
                source=source,
                cache=cache,
                core_version=core_version,
                trusted_keys=trusted_keys,
            )

    names = sorted(set(sources) & set(installed))
    candidates = list(await asyncio.gather(*(one(n) for n in names)))
    return CheckReport(checked_at=datetime.now(UTC), candidates=candidates)


__all__ = [
    "MAX_ARCHIVE_MEMBERS",
    "UNPACKED_SIZE_FACTOR",
    "ArchiveLimitError",
    "UnsupportedMemberError",
    "check_plugin",
    "installed_versions",
    "run_check",
    "verify_release_tarball",
]
