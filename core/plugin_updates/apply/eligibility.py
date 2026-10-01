"""Whether an available plugin update may be installed with one click here."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path

from core.config.plugin_update_apply import UpdateApplyConfig

from ..checker import _is_newer
from ..models import ApplyBlocker, PluginApplyStatus, Refusal, UpdateCandidate
from ..upgrade.method import _in_container
from ..upgrade_models import InstallMethod
from .models import ApplyRun, UpdaterHeartbeat

# ``custom`` is operator-written core-upgrade instructions: it says nothing
# about the deployment's shape, so the Kubernetes and container probes decide.
HOST_METHODS: tuple[InstallMethod, ...] = ("source", "pip", "custom")

_UNSIGNED = (None, Refusal.ARTIFACT_MISSING, Refusal.LEGACY_RELEASE)


def host_install_ok(
    method: InstallMethod,
    *,
    environ: Mapping[str, str] | None = None,
    root: Path = Path("/"),
) -> bool:
    """A host install: source/pip/custom, not Kubernetes, not a container.

    Kubernetes and containers block whatever ``SYSTEM_INSTALL_METHOD`` says.
    """
    env = os.environ if environ is None else environ
    if method not in HOST_METHODS or env.get("KUBERNETES_SERVICE_HOST", "").strip():
        return False
    return not _in_container(root)


def heartbeat_fresh(
    hb: UpdaterHeartbeat | None, config: UpdateApplyConfig, now: datetime
) -> bool:
    """The updater wrote a heartbeat within three periods."""
    return hb is not None and now - hb.at <= timedelta(
        seconds=3 * config.heartbeat_seconds
    )


def already_reached(candidate: UpdateCandidate, running: str | None) -> bool:
    """The running tree is already at (or past) the candidate's release."""
    latest = candidate.latest
    return bool(latest and running and not _is_newer(latest.version, running))


def _updater_reasons(
    hb: UpdaterHeartbeat | None, config: UpdateApplyConfig, now: datetime
) -> list[str]:
    """What a live updater says makes it unable to apply (empty when offline)."""
    if hb is None or not heartbeat_fresh(hb, config, now):
        return []
    reasons = []
    if not hb.enabled:
        reasons.append("updater disabled (UPDATE_APPLY_ENABLED off in the updater)")
    if not hb.restart_configured:
        reasons.append("updater has no restart command configured")
    return reasons


def _release_blocker(candidate: UpdateCandidate) -> tuple[ApplyBlocker | None, str]:
    assets = candidate.signed_assets
    if assets is None or (not assets.verified and assets.refusal in _UNSIGNED):
        return ApplyBlocker.UNSIGNED_RELEASE, ""
    if assets.verified:
        if assets.host_build_required:
            return ApplyBlocker.HOST_BUILD_REQUIRED, ""
        return None, ""
    if assets.refusal is Refusal.NEEDS_ENVIRONMENT_UPDATE:
        return ApplyBlocker.NEEDS_ENVIRONMENT_UPDATE, assets.detail
    text = (
        f"{assets.refusal}: {assets.detail}" if assets.detail else f"{assets.refusal}"
    )
    return ApplyBlocker.SIGNATURE_FAILED, text


def apply_status(
    candidate: UpdateCandidate,
    *,
    config: UpdateApplyConfig,
    method: InstallMethod,
    heartbeat: UpdaterHeartbeat | None,
    active_run: ApplyRun | None,
    core_version: str,
    now: datetime,
    environ: Mapping[str, str] | None = None,
    root: Path = Path("/"),
) -> PluginApplyStatus:
    """Every blocker, in console order; installable only when there is none."""
    if not candidate.available:
        return PluginApplyStatus(installable=False)
    blockers: list[ApplyBlocker] = []
    reasons = _updater_reasons(heartbeat, config, now)
    if not config.enabled or reasons:
        blockers.append(ApplyBlocker.APPLY_DISABLED)
    if not host_install_ok(method, environ=environ, root=root):
        blockers.append(ApplyBlocker.NOT_HOST_INSTALL)
    fresh = heartbeat is not None and heartbeat_fresh(heartbeat, config, now)
    if heartbeat is not None and fresh:
        if not (heartbeat.overlay_root and heartbeat.overlay_writable):
            blockers.append(ApplyBlocker.OVERLAY_UNCONFIGURED)
        if heartbeat.core_version != core_version:
            blockers.append(ApplyBlocker.UPDATER_MISMATCH)
    else:
        blockers.append(ApplyBlocker.UPDATER_OFFLINE)
    blocker, detail = _release_blocker(candidate)
    if blocker is not None:
        blockers.append(blocker)
    if reasons:
        detail = "; ".join(filter(None, [*reasons, detail]))
    if active_run is not None:
        blockers.append(ApplyBlocker.RUN_ACTIVE)
    return PluginApplyStatus(
        installable=not blockers,
        blockers=blockers,
        detail=detail,
        active_run=active_run.id if active_run else None,
    )


__all__ = [
    "HOST_METHODS",
    "already_reached",
    "apply_status",
    "heartbeat_fresh",
    "host_install_ok",
]
