"""Publish the update report as the ``baselith_update_available`` gauge."""

from __future__ import annotations

from core.observability.metrics import UPDATE_AVAILABLE

from .models import CheckReport

#: Every component this process has ever written, so one that later drops out of
#: the report (a plugin removed from the sources) is zeroed, not left at 1.
_known: set[str] = set()


def publish_update_metrics(report: CheckReport | None) -> None:
    """Make the gauge mirror ``report``; ``None`` reads as "nothing available".

    Every known component gets both ``security`` series written on every call:
    the active one to 1, the other to 0. Series are never removed, because in
    multiprocess mode a removal does not reach the mmap files and the alert
    would keep seeing the old 1. Plugin candidates carry no advisory data and
    publish ``security="false"``; ``core`` follows the system update's flag,
    and an advisory affecting the running version sets ``security="true"``
    even when no newer release exists yet.
    """
    active: set[tuple[str, str]] = set()
    if report is not None:
        _known.update(f"plugin:{c.plugin}" for c in report.candidates)
        active.update(
            (f"plugin:{c.plugin}", "false") for c in report.candidates if c.available
        )
        system = report.system
        # An advisory affecting the running version counts even when no fixed
        # release exists yet: the security alert must still fire.
        if system is not None and (system.available or system.security):
            active.add((system.component, "true" if system.security else "false"))
    _known.add("core")
    _known.update(component for component, _ in active)
    for component in _known:
        for security in ("true", "false"):
            value = 1 if (component, security) in active else 0
            UPDATE_AVAILABLE.labels(component, security).set(value)


__all__ = ["publish_update_metrics"]
