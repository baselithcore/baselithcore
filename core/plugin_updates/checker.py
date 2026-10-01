"""Check plugins for newer releases under the configured trust mode.

``signed`` releases are downloaded and verified here before being offered;
``provenance`` releases are judged by :mod:`.provenance` without a download.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import yaml

from core.plugins.discovery import apply_overlay
from core.plugins.integrity import find_manifest_file
from core.plugins.overlay import registered_overlay_dirs
from core.plugins.version import SemanticVersion

from .archive import (
    MAX_ARCHIVE_MEMBERS,
    UNPACKED_SIZE_FACTOR,
    ArchiveLimitError,
    UnsupportedMemberError,
    unpack_release,
    verify_release_tarball,
)
from .cache import UpdateCache
from .models import (
    CheckReport,
    Refusal,
    ReleaseInfo,
    ReleaseProvenance,
    SignedAssets,
    TrustMode,
    UpdateCandidate,
)
from .provenance import check_plugin_provenance
from .signed_assets import verify_signed_assets
from .sources import GitHubReleaseSource, SourceError, is_commit_sha

logger = logging.getLogger(__name__)

_CONCURRENCY = 4

# Kept for the existing tests and for scripts/plugin_mirrors/_release.py.
_unpack = unpack_release


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

    assets = await verify_signed_assets(
        plugin,
        latest,
        installed,
        source=source,
        cache=cache,
        core_version=core_version,
        trusted_keys=trusted_keys,
    )
    if not assets.verified:
        refused = _refused(
            plugin,
            installed,
            latest,
            assets.refusal or Refusal.MANIFEST_INVALID,
            assets.detail,
        )
        return refused.model_copy(update={"signed_assets": assets})
    return UpdateCandidate(
        plugin=plugin,
        installed_version=installed,
        latest=latest,
        available=True,
        signed_assets=assets,
    )


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
        candidate = await _check(
            plugin, slug, installed, source, cache, core_version, trusted_keys
        )
    except SourceError as exc:
        candidate = _refused(plugin, installed, None, Refusal.SOURCE_ERROR, str(exc))
    except Exception as exc:  # contain everything; the type name carries no secret
        logger.warning("plugin_update_check_failed: %s: %s", plugin, type(exc).__name__)
        candidate = _refused(
            plugin,
            installed,
            None,
            Refusal.SOURCE_ERROR,
            f"unexpected {type(exc).__name__}",
        )
    return _stamp_signed(candidate, slug, source)


def _stamp_signed(
    candidate: UpdateCandidate, slug: str, source: GitHubReleaseSource
) -> UpdateCandidate:
    """Mark a signed-mode candidate and record what its release says of itself.

    The commit is the release's ``target_commitish`` when that is a commit id;
    the signature, not this, is what the signed mode trusts.
    """
    latest = candidate.latest
    provenance = None
    if latest is not None:
        target = latest.target_commitish
        sha = target.lower() if target and is_commit_sha(target) else None
        provenance = ReleaseProvenance(
            author=latest.author,
            commit_sha=sha,
            commit_url=source.commit_url(slug, sha) if sha else None,
            published_at=latest.published_at,
        )
    return candidate.model_copy(update={"trust": "signed", "provenance": provenance})


async def _with_signed_assets(
    cand: UpdateCandidate,
    source: GitHubReleaseSource,
    cache: UpdateCache,
    core_version: str,
    trusted_keys: Sequence[str],
) -> UpdateCandidate:
    """Add the signed-asset verdict to an available provenance candidate."""
    if not cand.available or cand.latest is None:
        return cand
    try:
        assets = await verify_signed_assets(
            cand.plugin,
            cand.latest,
            cand.installed_version,
            source=source,
            cache=cache,
            core_version=core_version,
            trusted_keys=trusted_keys,
        )
    except Exception as exc:  # the notice must survive any asset failure
        logger.warning(
            "signed_assets_check_failed: %s: %s", cand.plugin, type(exc).__name__
        )
        assets = SignedAssets(
            verified=False,
            refusal=Refusal.SOURCE_ERROR,
            detail=f"unexpected {type(exc).__name__}",
        )
    return cand.model_copy(update={"signed_assets": assets})


async def run_check(
    sources: dict[str, str],
    installed: dict[str, str],
    *,
    source: GitHubReleaseSource,
    cache: UpdateCache,
    core_version: str,
    trusted_keys: Sequence[str],
    trust: TrustMode = "signed",
) -> CheckReport:
    """Check every plugin that has both a source and an installed version.

    ``trust`` selects the rule a release must pass to be offered:
    ``signed`` (:func:`check_plugin`) or ``provenance``
    (:func:`~.provenance.check_plugin_provenance`). In both modes an
    available release's signed assets are verified into
    ``UpdateCandidate.signed_assets``.
    """
    gate = asyncio.Semaphore(_CONCURRENCY)

    async def one(name: str) -> UpdateCandidate:
        async with gate:
            if trust == "provenance":
                cand = await check_plugin_provenance(
                    name,
                    sources[name],
                    installed[name],
                    source=source,
                    core_version=core_version,
                )
                return await _with_signed_assets(
                    cand, source, cache, core_version, trusted_keys
                )
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
