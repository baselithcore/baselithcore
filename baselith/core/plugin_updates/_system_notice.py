"""Pure helpers of a release notice: what is served, and what is announced.

The core notice (``system``) and a downstream distribution's notice
(``distribution``) are both :class:`SystemUpdate` values and follow the same
two rules, so they share these functions and differ only in their arguments.
"""

from __future__ import annotations

from .models import SystemUpdate


def scope_notice(
    notice: SystemUpdate | None,
    *,
    component: str,
    enabled: bool,
    repo: str,
    installed: str,
    guide_url: str | None,
) -> SystemUpdate | None:
    """Serve only a notice about this installation, with today's guide link.

    A saved notice of another component, another repository or another
    installed version (a cache from before an upgrade) is dropped rather than
    shown, and so is one saved while the check was on and read after it was
    switched off. ``guide_url`` always comes from the current configuration and
    the per-read ``upgrade`` instructions are cleared (they are stamped later,
    never cached).

    Args:
        notice: The saved or freshly checked notice.
        component: ``core`` or ``distribution``.
        enabled: Whether this notice's check is on.
        repo: The repository the check is configured for.
        installed: The version the notice must be about.
        guide_url: The upgrade guide link to serve with it.
    """
    if notice is None:
        return None
    if (
        not enabled
        or notice.component != component
        or notice.repo != repo
        or notice.installed_version != installed
    ):
        return None
    if notice.upgrade_guide_url == guide_url and notice.upgrade is None:
        return notice
    return notice.model_copy(update={"upgrade_guide_url": guide_url, "upgrade": None})


def notice_announcement(
    notice: SystemUpdate, *, notified_key_prefix: str
) -> tuple[str, dict[str, object]] | None:
    """The settled-once key and event payload of ``notice``, or None.

    None when no newer release is available. The key is
    ``<notified_key_prefix>:<latest version>`` (``system:1.4.0``,
    ``distribution:1.4.0``), so two notices with equal version strings never
    suppress each other.
    """
    version = notice.latest.version if notice.latest else None
    if not notice.available or version is None:
        return None
    return f"{notified_key_prefix}:{version}", {
        "component": notice.component,
        "repo": notice.repo,
        "installed_version": notice.installed_version,
        "latest_version": version,
        "behind": notice.behind,
        "major": notice.major,
        "security": notice.security,
        "severity": notice.severity,
    }


__all__ = ["notice_announcement", "scope_notice"]
