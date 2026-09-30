"""The stops of an upgrade that crosses major versions."""

from __future__ import annotations

from collections.abc import Sequence

from core.plugins.version import SemanticVersion

from ..models import ReleaseInfo


def upgrade_path(installed: str, releases: Sequence[ReleaseInfo]) -> list[str]:
    """Stops, in order, from ``installed`` to the latest release.

    A jump within one major version is direct (empty path). A jump across
    majors goes one major at a time: the latest release of the current major
    (when newer than ``installed``), the latest release of every intermediate
    major that has releases, then the latest release. Skipping a major would
    skip the migrations and deprecation windows that major's releases carry.

    Args:
        installed: The running core release.
        releases: Published stable releases, in any order.

    Returns:
        The release versions to install in turn; empty for a direct upgrade or
        when nothing newer exists.
    """
    try:
        current = SemanticVersion(installed)
    except ValueError:
        return []
    newer: list[tuple[SemanticVersion, str]] = []
    for release in releases:
        try:
            version = SemanticVersion(release.version)
        except ValueError:
            continue
        if version > current:
            newer.append((version, release.version))
    if not newer:
        return []
    newer.sort(key=lambda pair: pair[0])
    target = newer[-1]
    if target[0].major == current.major:
        return []
    latest_per_major: dict[int, str] = {}
    for version, text in newer:
        latest_per_major[version.major] = text  # ascending: the last one wins
    stops = [
        latest_per_major[major]
        for major in range(current.major, target[0].major)
        if major in latest_per_major
    ]
    return [*stops, target[1]]


__all__ = ["upgrade_path"]
