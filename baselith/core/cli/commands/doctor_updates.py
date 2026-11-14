"""The ``Updates`` check of ``baselith doctor``.

Reads the cached update report of the last check and says whether a core or
plugin update is waiting. It never contacts GitHub and never fails the doctor
run: a security update is a non-blocking warning, and a missing or unreadable
report is a pass with a note.
"""

from __future__ import annotations

import logging

from baselith.core.cli.commands._update_report import (
    load_served_report,
    release_error,
)
from baselith.core.cli.commands.doctor_checks import CheckResult
from baselith.core.config.plugin_updates import get_plugin_update_config
from baselith.core.plugin_updates.models import CheckReport, Refusal

logger = logging.getLogger(__name__)

_NAME = "Updates"
_SEE = "See: baselith update status"


def _load_cached() -> CheckReport | None:
    """The last saved report as the server serves it, or None."""
    return load_served_report()


def _failure(report: CheckReport) -> str | None:
    """Why the last check failed, or None when it did not."""
    if report.error:
        return report.error
    if (core := release_error(report.system)) is not None:
        return core
    for c in report.candidates:
        if c.refusal == Refusal.SOURCE_ERROR:
            return f"{c.plugin}: {c.detail or 'source error'}"
    return None


def check_updates() -> CheckResult:
    """Summarise the last update report.

    Returns:
        A warning (``passed=False``, ``severity="warn"``) when a security
        advisory affects the installed core, whether or not a fixed release
        exists; otherwise a passing result describing the available updates,
        a failed last check, switched-off checks, the up-to-date state, a
        missing report or an unreadable one. Never a ``severity="fail"``
        result.
    """
    try:
        report = _load_cached()
        config = get_plugin_update_config()
    except Exception as exc:  # an unreadable cache must not break the doctor
        logger.debug("update report unreadable", exc_info=True)
        return CheckResult(_NAME, True, "Update report unreadable", type(exc).__name__)
    if not config.enabled:
        return CheckResult(
            _NAME,
            True,
            "Update checks off",
            "Set CORE_UPDATE_REPO and/or PLUGIN_UPDATE_SOURCES_FILE to turn them on",
        )
    if report is None:
        return CheckResult(
            _NAME,
            True,
            "No update report yet",
            "Run: baselith update check",
        )
    system = report.system
    latest = system.latest.version if system and system.latest else None
    fixed = latest if system and system.available else None
    if system and system.security:
        message = f"Security advisory affects core {system.installed_version}"
        if fixed:
            message += f"; fixed in {fixed}"
        return CheckResult(_NAME, False, message, _SEE, "warn")
    plugins = sum(1 for c in report.candidates if c.available)
    if fixed or plugins:
        parts = []
        if fixed:
            parts.append(f"core {fixed}")
        if plugins:
            parts.append(f"{plugins} plugin update(s)")
        return CheckResult(_NAME, True, "Update available: " + ", ".join(parts), _SEE)
    if (failure := _failure(report)) is not None:
        return CheckResult(
            _NAME, True, f"Last check failed ({failure})", "Run: baselith update check"
        )
    if system is None and config.system_checks_enabled:
        # the saved notice is about another core version or repository
        return CheckResult(
            _NAME,
            True,
            "No update report yet",
            "The saved report is about another core version; "
            "run: baselith update check",
        )
    return CheckResult(
        _NAME, True, f"Up to date (checked {report.checked_at.date().isoformat()})"
    )


__all__ = ["check_updates"]
