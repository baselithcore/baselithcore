"""System (core) update check: newer releases and security advisories.

The running core is always referenced against the public core project: the
installed version is the public core release the tree corresponds to
(``baselith.core._core_version.CORE_VERSION``), never a downstream distribution's own
version, and the repo is the public core repository (``CORE_UPDATE_REPO``).
Notice only: nothing here installs or downloads anything.
"""

from __future__ import annotations

import asyncio
import logging

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from baselith.core.plugins.version import SemanticVersion

from ._advisories import SEVERITY_ORDER
from .models import Advisory, ReleaseInfo, SystemUpdate
from .sources import (
    AdvisoriesNotPublished,
    GitHubReleaseSource,
    SourceError,
    safe_error,
)
from .upgrade.path import upgrade_path

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

    An unparseable or missing range, or an unparseable installed version,
    cannot rule the advisory out, so it *counts* — logged as uncertain —
    rather than quietly dropping a real advisory from the notice.
    """
    try:
        if not advisory.vulnerable_range.strip():
            raise InvalidSpecifier("empty range")
        return _specifier(advisory.vulnerable_range).contains(
            Version(version), prereleases=True
        )
    except (InvalidSpecifier, InvalidVersion):
        logger.warning(
            "system_update_range_uncertain: %s %r vs %r — reported as affecting",
            advisory.ghsa_id,
            advisory.vulnerable_range,
            version,
        )
        return True


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
            upgrade_path=previous.upgrade_path,
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
    """Compare the running core with the public core's releases and advisories.

    Args:
        installed: The public core release the running tree corresponds to.
        slug: ``owner/repo`` of the public core project.
        source: The GitHub source to query.
        previous: The last saved result; the state of a part that fails to
            refresh is carried over, and so is a known security state when the
            advisories endpoint answers 404 (no error is set for it). Only a
            successful fetch that does not match clears the security flag.

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
    # The two lookups are independent requests to the same host: one round
    # trip of waiting, not two. Each outcome is judged on its own below, and an
    # error this function does not handle propagates exactly as before.
    fetched_releases, fetched_advisories = await asyncio.gather(
        source.list_releases(_COMPONENT_NAME, slug),
        source.security_advisories(slug),
        return_exceptions=True,
    )
    if isinstance(fetched_releases, SourceError):
        releases_failed = True
        errors.append(safe_error(fetched_releases))
    elif isinstance(fetched_releases, BaseException):
        raise fetched_releases
    else:
        releases = fetched_releases
    newer = [r for r in releases if SemanticVersion(r.version) > current]
    latest = releases[0] if releases else None
    matching: list[Advisory] = []
    if isinstance(fetched_advisories, AdvisoriesNotPublished):
        advisories_failed = True
    elif isinstance(fetched_advisories, SourceError):
        advisories_failed = True
        errors.append(f"advisories unavailable ({safe_error(fetched_advisories)})")
    elif isinstance(fetched_advisories, BaseException):
        raise fetched_advisories
    else:
        seen: set[str] = set()
        for adv in fetched_advisories:
            if affects(adv, installed) and adv.ghsa_id not in seen:
                seen.add(adv.ghsa_id)
                matching.append(adv)
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
            "upgrade_path": upgrade_path(installed, releases),
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
