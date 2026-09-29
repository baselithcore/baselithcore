"""System (framework) update check: newer releases and security advisories.

Notice only: nothing here installs or downloads anything.
"""

from __future__ import annotations

import logging

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from core.plugins.version import SemanticVersion

from ._advisories import SEVERITY_ORDER
from .models import Advisory, ReleaseInfo, SystemUpdate
from .sources import GitHubReleaseSource, SourceError, safe_error

logger = logging.getLogger(__name__)

_COMPONENT_NAME = "core"


def _specifier(vulnerable_range: str) -> SpecifierSet:
    """Convert a GitHub range (``>= 1.0.0, < 1.0.4``, ``= 1.2.3``) to a set."""
    clauses = []
    for clause in vulnerable_range.split(","):
        clause = clause.strip()
        if clause.startswith("=") and not clause.startswith("=="):
            clause = "=" + clause
        clauses.append(clause.replace(" ", ""))
    return SpecifierSet(",".join(clauses))


def affects(advisory: Advisory, version: str) -> bool:
    """Whether ``version`` lies in the advisory's vulnerable range.

    An unparseable range or version is logged and treated as not matching.
    """
    try:
        if not advisory.vulnerable_range.strip():
            raise InvalidSpecifier("empty range")
        return _specifier(advisory.vulnerable_range).contains(
            Version(version), prereleases=True
        )
    except (InvalidSpecifier, InvalidVersion):
        logger.warning(
            "system_update_range_unparseable: %s %r",
            advisory.ghsa_id,
            advisory.vulnerable_range,
        )
        return False


def carry_over(
    new: SystemUpdate,
    previous: SystemUpdate | None,
    *,
    releases: bool,
    advisories: bool,
) -> SystemUpdate:
    """Keep the last known state for the part of a check that failed.

    Only applies to the same installed version and repo: after an upgrade the
    old state is stale and is never carried over. ``error`` on ``new`` is kept.

    Args:
        new: The fresh (partly failed) result.
        previous: The last saved result, if any.
        releases: True when the release lookup failed.
        advisories: True when the advisory lookup failed.
    """
    if (
        previous is None
        or previous.installed_version != new.installed_version
        or previous.repo != new.repo
    ):
        return new
    update: dict[str, object] = {}
    if releases:
        update.update(
            latest=previous.latest,
            available=previous.available,
            behind=previous.behind,
            major=previous.major,
        )
    if advisories:
        update.update(
            advisories=previous.advisories,
            security=previous.security,
            severity=previous.severity,
        )
    return new.model_copy(update=update)


async def check_system(
    installed: str,
    slug: str,
    *,
    source: GitHubReleaseSource,
    previous: SystemUpdate | None = None,
) -> SystemUpdate:
    """Compare the running framework with its repo's releases and advisories.

    Args:
        installed: The running framework version.
        slug: ``owner/repo`` of the distribution.
        source: The GitHub source to query.
        previous: The last saved result; the state of a part that fails to
            refresh is carried over (a fetched-and-empty advisory list still
            clears the security flag).

    Returns:
        The status. The release lookup and the advisory lookup fail
        independently: an advisory failure (a token without the advisories
        scope) still reports the update, a release failure sets ``error``.
    """
    result = SystemUpdate(repo=slug, installed_version=installed)
    errors: list[str] = []
    releases_failed = advisories_failed = False
    releases: list[ReleaseInfo] = []
    try:
        current = SemanticVersion(installed)
    except ValueError:
        return result.model_copy(update={"error": "invalid installed version"})
    try:
        releases = await source.list_releases(_COMPONENT_NAME, slug)
    except SourceError as exc:
        releases_failed = True
        errors.append(safe_error(exc))
    newer = [r for r in releases if SemanticVersion(r.version) > current]
    latest = releases[0] if releases else None
    matching: list[Advisory] = []
    try:
        seen: set[str] = set()
        for adv in await source.security_advisories(slug):
            if affects(adv, installed) and adv.ghsa_id not in seen:
                seen.add(adv.ghsa_id)
                matching.append(adv)
    except SourceError as exc:
        advisories_failed = True
        errors.append(f"advisories unavailable ({safe_error(exc)})")
    severity = max(
        (a.severity for a in matching), key=lambda s: SEVERITY_ORDER[s], default=None
    )
    fresh = result.model_copy(
        update={
            "latest": latest,
            "available": bool(newer),
            "behind": len(newer),
            "major": bool(newer)
            and SemanticVersion(newer[0].version).major > current.major,
            "security": bool(matching),
            "severity": severity,
            "advisories": matching,
            "error": "; ".join(errors) or None,
        }
    )
    return carry_over(
        fresh, previous, releases=releases_failed, advisories=advisories_failed
    )


__all__ = ["affects", "carry_over", "check_system"]
