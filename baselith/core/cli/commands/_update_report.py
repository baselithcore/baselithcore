"""What ``baselith update`` and ``baselith doctor`` share about the update report.

Both read the report the way the server serves it (the same current-trust,
scope and already-installed filters), never the raw cache file, and both need
to tell a failed release lookup from an unavailable advisories endpoint.
"""

from __future__ import annotations

import logging
import re

from baselith.core.config.plugin_updates import get_plugin_update_config
from baselith.core.plugin_updates.models import CheckReport, Refusal, SystemUpdate
from baselith.core.plugin_updates.service import PluginUpdateService

logger = logging.getLogger(__name__)

# ``check_system`` appends the advisories failure last, as this one segment.
_ADVISORIES_SEGMENT = re.compile(r"(?:^|; )advisories unavailable \(.*\)\Z", re.DOTALL)


def load_served_report() -> CheckReport | None:
    """The last saved report as the server would serve it, or None.

    Constructing the service connects to nothing (the announcement gate's Redis
    client is built lazily) and writes nothing; ``report()`` only reads. Every
    failure, an unreadable cache included, answers None.
    """
    try:
        return PluginUpdateService(get_plugin_update_config()).report()
    except Exception:  # a diagnostic must not break on a bad cache or config
        logger.debug("update report could not be loaded", exc_info=True)
        return None


def release_error(system: SystemUpdate | None) -> str | None:
    """The release-lookup part of ``system.error``, without the advisories part.

    A token without the advisories scope sets ``system.error`` on every check;
    that alone is not a failed check.
    """
    if system is None or not system.error:
        return None
    rest = _ADVISORIES_SEGMENT.sub("", system.error).strip("; ").strip()
    return rest or None


def check_failed(report: CheckReport) -> bool:
    """Whether the check itself failed (the ``check`` exit rule).

    True when the plugin part set ``error``, any plugin verdict is a source
    error, or the core or distribution release lookup failed. An unavailable
    advisories endpoint alone is not a failure.
    """
    return bool(
        report.error
        or release_error(report.system)
        or release_error(report.distribution)
        or any(c.refusal == Refusal.SOURCE_ERROR for c in report.candidates)
    )


__all__ = ["check_failed", "load_served_report", "release_error"]
