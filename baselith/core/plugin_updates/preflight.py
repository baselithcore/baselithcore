"""Pre-flight: whether an update can be installed now, as a list a person can act on.

Each :class:`PreflightItem` is ``ok``, ``warn`` or ``block`` with one sentence.
:func:`build_preflight` is pure: the report, the settings, the updater's
heartbeat, the active run, the installed plugins' core bounds and every probe
are passed in, and a probe that raises becomes a ``warn`` item
(``<probe>_unavailable``), never an exception. ``ready`` closes the list only
when nothing blocks. Computed on request; the updater checks again before it
touches anything.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from baselith.core.config.plugin_update_apply import UpdateApplyConfig
from baselith.core.plugins.version import SemanticVersion

from ._core_preflight import CoreState, core_blocker_items
from ._preflight_items import BLOCKER_TEXT as _BLOCKER_TEXT
from ._preflight_items import PreflightItem, PreflightLevel
from ._preflight_items import block as _block
from ._preflight_items import item as _item
from ._preflight_items import warn as _warn
from .apply.eligibility import already_reached, apply_status
from .apply.models import (
    CORE_CLAIM,
    CORE_SUBJECT,
    PLUGIN_SUBJECT_PREFIX,
    ApplyRun,
    UpdaterHeartbeat,
)
from .models import CheckReport, Refusal, SystemUpdate, UpdateCandidate
from .upgrade.compat import plugin_compatibility
from .upgrade_models import InstallMethod, PluginBoundsIssue

logger = logging.getLogger(__name__)

_MB = 1024 * 1024


def normalize_subject(subject: str) -> str:
    """``core`` or ``plugin:<name>``; a bare plugin name gains the prefix."""
    text = subject.strip()
    if text == CORE_SUBJECT or text.startswith(PLUGIN_SUBJECT_PREFIX):
        return text
    return f"{PLUGIN_SUBJECT_PREFIX}{text}"


def _core_notice(report: CheckReport, distribution: bool) -> SystemUpdate | None:
    """The installable core notice: the distribution's in a distribution."""
    return report.distribution if distribution else report.system


def _core_candidate(
    report: CheckReport | None, version: str | None, distribution: bool
) -> tuple[UpdateCandidate | None, SystemUpdate | None, list[PreflightItem]]:
    notice = _core_notice(report, distribution) if report is not None else None
    if notice is None or notice.latest is None:
        what = "distribution" if distribution else "core"
        return (
            None,
            None,
            [
                _block(
                    "candidate_unknown",
                    f"No {what} release is known: run `baselith update check` first.",
                )
            ],
        )
    target = version or notice.latest.version
    if target == notice.installed_version or (
        notice.latest.version == target and not notice.available
    ):
        return (
            None,
            notice,
            [_block("already_installed", f"{target} is already installed.")],
        )
    if target != notice.latest.version:
        return (
            None,
            notice,
            [
                _block(
                    "candidate_unknown",
                    f"{target} is not the latest known release "
                    f"({notice.latest.version}); only that one can be installed.",
                )
            ],
        )
    candidate = UpdateCandidate(
        plugin=CORE_CLAIM,
        installed_version=notice.installed_version,
        latest=notice.latest,
        available=True,
    )
    return candidate, notice, []


def _plugin_candidate(
    report: CheckReport | None, name: str, version: str | None
) -> tuple[UpdateCandidate | None, list[PreflightItem]]:
    found = next(
        (c for c in (report.candidates if report else []) if c.plugin == name), None
    )
    if found is None or found.latest is None:
        return None, [
            _block(
                "candidate_unknown",
                f"No release of {name} is known: run `baselith update check` first.",
            )
        ]
    target = version or found.latest.version
    reached = target == found.installed_version or already_reached(
        found, found.installed_version
    )
    if reached or (not found.available and found.refusal in (None, Refusal.NOT_NEWER)):
        return None, [_block("already_installed", f"{name} {target} is installed.")]
    if not found.available:
        return None, [
            _block(
                "candidate_unknown",
                f"The release of {name} was refused ({found.refusal}).",
            )
        ]
    if target != found.latest.version:
        return None, [
            _block(
                "candidate_unknown",
                f"{name} {target} is not the latest known release "
                f"({found.latest.version}); only that one can be installed.",
            )
        ]
    return found, []


def _blocker_items(
    candidate: UpdateCandidate,
    *,
    config: UpdateApplyConfig,
    method: InstallMethod,
    heartbeat: UpdaterHeartbeat | None,
    active_run: ApplyRun | None,
    core_version: str,
    now: datetime,
) -> list[PreflightItem]:
    status = apply_status(
        candidate,
        config=config,
        method=method,
        heartbeat=heartbeat,
        active_run=active_run,
        core_version=core_version,
        now=now,
    )
    suffix = f" ({status.detail})" if status.detail else ""
    return [
        _block(f"blocker_{b.value}", f"{_BLOCKER_TEXT.get(b, b.value)}{suffix}")
        for b in status.blockers
    ]


def _compat_items(
    notice: SystemUpdate | None,
    target: str,
    bounds: list[PluginBoundsIssue] | None,
    distribution: bool,
) -> list[PreflightItem]:
    if distribution:
        return [
            _warn(
                "plugin_compatibility_unknown",
                "Plugin compatibility cannot be checked yet: the release does not "
                "say which public core it contains.",
            )
        ]
    compat = plugin_compatibility(target, bounds)
    if not compat.checked:
        return [_warn("plugin_compatibility_unknown", compat.reason or "")]
    if not compat.incompatible:
        return []
    names = ", ".join(sorted(b.plugin for b in compat.incompatible))
    return [
        _warn(
            "plugin_incompatible",
            f"These plugins declare core bounds that exclude {target}: {names}.",
        )
    ]


def _major_items(installed: str | None, target: str, core: bool) -> list[PreflightItem]:
    try:
        if installed is None:
            return []
        jump = SemanticVersion(target).major > SemanticVersion(installed).major
    except ValueError:
        return []
    if not jump:
        return []
    text = f"{installed} to {target} crosses a major version."
    if core:
        return [_block("major_jump", f"{text} Follow the upgrade instructions.")]
    return [_warn("major_jump", f"{text} Read the release notes first.")]


def _probe_items(
    queue_probe: Callable[[], int],
    disk_free: Callable[[Path], int],
    disk_path: Path,
    min_free_mb: int,
) -> list[PreflightItem]:
    items: list[PreflightItem] = []
    try:
        jobs = queue_probe()
    except Exception:  # a probe never fails the pre-flight
        logger.debug("preflight queue probe failed", exc_info=True)
        items.append(
            _warn("queue_probe_unavailable", "The task queue could not be read.")
        )
    else:
        if jobs > 0:
            items.append(
                _warn("queue_busy", f"{jobs} queue job(s) are waiting or running.")
            )
    try:
        free = disk_free(disk_path)
    except Exception:
        logger.debug("preflight disk probe failed", exc_info=True)
        items.append(_warn("disk_probe_unavailable", "Free disk space is unknown."))
    else:
        if free < min_free_mb * _MB:
            items.append(
                _warn(
                    "disk_low",
                    f"Only {free // _MB} MB free at {disk_path} "
                    f"(UPDATE_APPLY_MIN_FREE_MB is {min_free_mb}).",
                )
            )
    return items


def _boot_items(boot_probe: Callable[[], list[str] | None]) -> list[PreflightItem]:
    try:
        active = boot_probe()
    except Exception:
        logger.debug("preflight boot report probe failed", exc_info=True)
        return [_warn("boot_report_unavailable", "The boot report could not be read.")]
    if active is None:
        return [
            _warn(
                "no_boot_report",
                "No boot report yet: restart the API once with UPDATE_APPLY_ENABLED "
                "on, or the run will be refused.",
            )
        ]
    return []


def build_preflight(
    subject: str,
    version: str | None,
    *,
    report: CheckReport | None,
    config: UpdateApplyConfig,
    heartbeat: UpdaterHeartbeat | None,
    active_run: ApplyRun | None,
    bounds: list[PluginBoundsIssue] | None,
    queue_probe: Callable[[], int],
    disk_free: Callable[[Path], int],
    now: datetime,
    method: InstallMethod,
    core_version: str,
    distribution: bool,
    boot_probe: Callable[[], list[str] | None] | None = None,
    core: CoreState | None = None,
) -> list[PreflightItem]:
    """The pre-flight of installing ``version`` of ``subject`` here, now.

    Args:
        subject: ``core`` or ``plugin:<name>`` (a bare name is a plugin).
        version: The target release; None means the latest known one.
        report: The update report as served.
        config: The one-click settings (``UPDATE_APPLY_*``).
        heartbeat: The updater's last heartbeat.
        active_run: The run holding the subject's claim, if any.
        bounds: The installed plugins' core bounds (None: unreadable).
        queue_probe: Jobs waiting or running in the task queue.
        disk_free: Free bytes on the filesystem of a path.
        now: The current time (timezone-aware).
        method: This deployment's installation method.
        core_version: The public core release the API runs.
        distribution: Whether this tree is a distribution (the core subject
            then means the distribution's own release).
        boot_probe: Active plugins of the newest boot report (None: none yet).
        core: The host's state and the verified release, for the core
            subject (None: nothing known, which blocks as unknown).

    Returns:
        The items; never raises for a failing probe.
    """
    key = normalize_subject(subject)
    is_core = key == CORE_SUBJECT
    notice: SystemUpdate | None = None
    if is_core:
        candidate, notice, items = _core_candidate(report, version, distribution)
    else:
        name = key.removeprefix(PLUGIN_SUBJECT_PREFIX)
        candidate, items = _plugin_candidate(report, name, version)
    if candidate is None or candidate.latest is None:
        return items
    target = candidate.latest.version
    state = core or CoreState(installed_version=candidate.installed_version or "")
    if is_core:  # a core install has its own guards: no overlay, a clean checkout
        items += core_blocker_items(
            state,
            config=config,
            method=method,
            heartbeat=heartbeat,
            active_run=active_run,
            core_version=core_version,
            now=now,
        )
        items += _compat_items(notice, target, bounds, distribution)
    else:
        items += _blocker_items(
            candidate,
            config=config,
            method=method,
            heartbeat=heartbeat,
            active_run=active_run,
            core_version=core_version,
            now=now,
        )
    if not is_core or state.release is None:  # else blocker_major_jump says it
        items += _major_items(candidate.installed_version, target, is_core)
    if notice is not None and notice.security:
        severity = notice.severity or "unknown"
        items.append(
            _item(
                "security_advisory",
                PreflightLevel.OK,
                f"A security advisory ({severity}) affects the installed "
                "version: read it before deciding when to update.",
            )
        )
    disk_path = (
        Path(heartbeat.overlay_root)
        if heartbeat is not None and heartbeat.overlay_root
        else config.state_dir
    )
    items += _probe_items(queue_probe, disk_free, disk_path, config.min_free_mb)
    if boot_probe is not None:
        items += _boot_items(boot_probe)
    if not any(i.level is PreflightLevel.BLOCK for i in items):
        items.append(
            _item("ready", PreflightLevel.OK, f"{target} can be installed now.")
        )
    return items


__all__ = [
    "CoreState",
    "PreflightItem",
    "PreflightLevel",
    "build_preflight",
    "normalize_subject",
]
