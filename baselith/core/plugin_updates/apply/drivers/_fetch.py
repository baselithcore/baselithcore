"""Download the signed manifest of one distribution release.

One GitHub API lookup of the release tagged ``v<version>`` in the
distribution's repository, then its ``distribution-release.json`` asset,
size-capped like every metadata asset (the plugin fetcher's download path,
:func:`~.._io.download_text_asset`). Nothing here verifies the bytes: the
caller does, with :func:`~...distribution_manifest.load_distribution_manifest`.
"""

from __future__ import annotations

from typing import Protocol

from ...distribution_manifest import DISTRIBUTION_ASSET
from ...sources import GitHubReleaseSource, SourceError
from .._io import download_text_asset

#: The ``plugin`` label the release parser gets for a distribution release.
_LABEL = "distribution"


class DistributionFetcher(Protocol):
    """Downloads the raw ``distribution-release.json`` of a release."""

    async def manifest(self, version: str) -> bytes: ...


class GitHubDistributionFetcher:
    """Reads ``distribution-release.json`` from the GitHub release ``v<version>``."""

    def __init__(self, source: GitHubReleaseSource, slug: str) -> None:
        """Create a fetcher for the repository ``slug`` (``owner/name``)."""
        self._source = source
        self._slug = slug

    async def manifest(self, version: str) -> bytes:
        """The raw manifest of release ``version``.

        Raises:
            SourceError: No repository configured, no such release, no
                manifest asset, or the download failed or was too large.
        """
        if not self._slug:
            raise SourceError("no distribution repository configured")
        tag = f"v{version}"
        info = await self._source.release_by_tag(_LABEL, self._slug, tag)
        if info is None:
            raise SourceError(f"release {tag} not found")
        if not info.distribution_manifest_url:
            raise SourceError(f"release {tag} has no {DISTRIBUTION_ASSET} asset")
        return await download_text_asset(self._source, info.distribution_manifest_url)


__all__ = ["DistributionFetcher", "GitHubDistributionFetcher"]
