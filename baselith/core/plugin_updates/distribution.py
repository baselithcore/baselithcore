"""The distribution notice: a downstream distribution's own releases.

A distribution (this tree's ``_version.py`` carries ``__distribution__``)
versions independently of the public core release it contains. The core
notice compares ``CORE_VERSION`` with the public core; this one compares the
distribution's ``__version__`` with ``DISTRIBUTION_UPDATE_REPO``. In the public
core the marker is absent and the two would be the same project, so this
notice is never built there.

The notice never carries the core's upgrade guide link nor the core's
upgrade instructions: those describe upgrading the public core.
"""

from __future__ import annotations

import logging

from baselith.core import _version
from baselith.core.config.plugin_updates import PluginUpdateConfig

from ._system_notice import scope_notice
from .models import CheckReport, SystemUpdate
from .sources import GitHubReleaseSource, safe_error
from .system import carry_over, check_system

logger = logging.getLogger(__name__)

COMPONENT = "distribution"
#: Prefix of the settled-once announcement key: ``distribution:<version>``.
NOTIFIED_KEY_PREFIX = "distribution"


def distribution_name() -> str | None:
    """The distribution's name (``__distribution__``), None in the public core."""
    marker = getattr(_version, "__distribution__", None)
    return marker if isinstance(marker, str) and marker.strip() else None


def installed_distribution_version() -> str:
    """The distribution's own version (``__version__``)."""
    return str(_version.__version__)


async def check_distribution(
    installed: str,
    slug: str,
    *,
    source: GitHubReleaseSource,
    previous: SystemUpdate | None,
) -> SystemUpdate:
    """Compare the distribution's version with its repository's releases.

    The same lookup as the core notice (:func:`~.system.check_system`: newer
    releases, advisories, carry-over of a failed part), labelled
    ``component="distribution"``.
    """
    notice = await check_system(installed, slug, source=source, previous=previous)
    return notice.model_copy(update={"component": COMPONENT})


def scope_distribution(
    notice: SystemUpdate | None, config: PluginUpdateConfig, installed: str
) -> SystemUpdate | None:
    """Serve only a distribution notice about this installation.

    Dropped when the check is off (no repo, or the public core), when it is
    about another repository or another installed version, or when it is not
    a distribution notice. Never stamped with the core's guide link.
    """
    return scope_notice(
        notice,
        component=COMPONENT,
        enabled=config.distribution_checks_enabled,
        repo=config.distribution_update_repo.strip(),
        installed=installed,
        guide_url=None,
    )


async def distribution_part(
    config: PluginUpdateConfig,
    source: GitHubReleaseSource,
    previous: CheckReport | None,
) -> SystemUpdate | None:
    """The distribution part of a check; None when its check is off.

    Fails independently of the core part: an unexpected error is logged by
    type, reported in ``error`` and the last known state is carried over.
    """
    if not config.distribution_checks_enabled:
        return None
    slug = config.distribution_update_repo.strip()
    installed = installed_distribution_version()
    prior = previous.distribution if previous else None
    try:
        notice = await check_distribution(
            installed, slug, source=source, previous=prior
        )
    except Exception as exc:
        logger.warning("distribution_update_check_failed: %s", type(exc).__name__)
        bare = SystemUpdate(
            component=COMPONENT,
            repo=slug,
            installed_version=installed,
            error=safe_error(exc),
        )
        notice = carry_over(bare, prior, releases=True, advisories=True)
    return scope_distribution(notice, config, installed)


__all__ = [
    "COMPONENT",
    "NOTIFIED_KEY_PREFIX",
    "check_distribution",
    "distribution_name",
    "distribution_part",
    "installed_distribution_version",
    "scope_distribution",
]
