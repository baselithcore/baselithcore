"""What stands between a host and a one-click core install, in console order.

The core is installed in place from a signed commit (``core-host`` driver), so
its blockers differ from a plugin's: no overlay is involved, the checkout must
be clean, a backup command must exist, ``uv.lock`` must not change (nothing
installs a package implicitly) and a ``breaking`` schema migration or a major
jump is never installed with one click. The API computes them for the console
and the pre-flight; the driver computes them again, fresh, before it touches
anything. Pure: every input is passed in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from baselith.core.config.plugin_update_apply import UpdateApplyConfig
from baselith.core.plugins.version import SemanticVersion

from ...distribution_manifest import DistributionManifest, Migrations
from ...models import ApplyBlocker
from ...upgrade_models import InstallMethod
from ..eligibility import heartbeat_fresh, host_install_ok
from ..models import ApplyRun, UpdaterHeartbeat

#: The only installation method the core host driver installs: a git checkout.
CORE_METHODS: tuple[InstallMethod, ...] = ("source",)

#: Blockers only a core verdict carries (a plugin verdict never does).
CORE_ONLY_BLOCKERS = frozenset(
    {
        ApplyBlocker.BACKUP_UNCONFIGURED,
        ApplyBlocker.UPDATER_RESTART_UNCONFIGURED,
        ApplyBlocker.TREE_DIRTY,
        ApplyBlocker.BREAKING_MIGRATION,
        ApplyBlocker.MAJOR_JUMP,
        ApplyBlocker.CORE_DISABLED,
        ApplyBlocker.CORE_INSTALL_UNSUPPORTED,
        ApplyBlocker.CORE_TOO_OLD,
    }
)


class CoreRelease(BaseModel):
    """What a verified distribution manifest pins, as the guards need it."""

    model_config = ConfigDict(frozen=True)

    version: str
    commit_sha: str
    lock_sha256: str
    migrations: Migrations
    #: The signed container image digest (None: the release ships none).
    image_digest: str | None = None
    #: The oldest installed public core (``CORE_VERSION``) the release may be
    #: installed over (None: any).
    min_core_version: str | None = None

    @classmethod
    def of(cls, manifest: DistributionManifest) -> CoreRelease:
        """The release a verified manifest describes."""
        return cls(
            version=manifest.version,
            commit_sha=manifest.commit_sha,
            lock_sha256=manifest.lock_sha256,
            migrations=manifest.migrations,
            image_digest=manifest.image_digest,
            min_core_version=manifest.min_core_version,
        )


def _porcelain_paths(line: str) -> list[str]:
    """The path(s) of one ``git status --porcelain`` v1 line (both of a rename)."""
    body = line[3:]
    parts = body.split(" -> ") if line[:1] in ("R", "C") else [body]
    return [p.strip().strip('"') for p in parts if p.strip()]


def _ignored(path: str, ignored: Sequence[str]) -> bool:
    for prefix in ignored:
        base = prefix.rstrip("/")
        if path == base or path.startswith(f"{base}/"):
            return True
    return False


def tree_is_dirty(porcelain: str, ignored: Sequence[str]) -> bool:
    """Any tracked change outside ``ignored`` (prefixes on a path boundary).

    Untracked files (``??``) never count: they are runtime state a checkout
    accumulates, and a checkout of another commit leaves them alone.
    """
    for line in porcelain.splitlines():
        if len(line) < 4 or line.startswith("??") or line.startswith("!!"):
            continue
        if any(not _ignored(p, ignored) for p in _porcelain_paths(line)):
            return True
    return False


def _major_differs(installed: str, target: str) -> bool:
    try:
        return SemanticVersion(installed).major != SemanticVersion(target).major
    except ValueError:  # an unreadable version is never a silent "same major"
        return True


def _host_blockers(
    *,
    config: UpdateApplyConfig,
    installed_version: str,
    tree_dirty: bool | None,
    install_method: InstallMethod,
    heartbeat: UpdaterHeartbeat | None,
    core_version: str,
    now: datetime,
    environ: Mapping[str, str] | None,
    root: Path,
) -> list[ApplyBlocker]:
    found: list[ApplyBlocker] = []
    if not config.core_enabled:
        found.append(ApplyBlocker.CORE_DISABLED)
    if not host_install_ok(install_method, environ=environ, root=root):
        found.append(ApplyBlocker.NOT_HOST_INSTALL)
    elif install_method not in CORE_METHODS:
        found.append(ApplyBlocker.CORE_INSTALL_UNSUPPORTED)
    fresh = heartbeat is not None and heartbeat_fresh(heartbeat, config, now)
    live_off = (
        fresh
        and heartbeat is not None
        and not (heartbeat.enabled and heartbeat.restart_configured)
    )
    if not config.enabled or live_off:
        found.append(ApplyBlocker.APPLY_DISABLED)
    if heartbeat is None or not fresh:
        found.append(ApplyBlocker.UPDATER_OFFLINE)
    elif heartbeat.core_version != core_version or (
        heartbeat.framework_version is not None
        and heartbeat.framework_version != installed_version
    ):
        found.append(ApplyBlocker.UPDATER_MISMATCH)
    if not config.backup_configured:
        found.append(ApplyBlocker.BACKUP_UNCONFIGURED)
    if not config.updater_restart_command:
        found.append(ApplyBlocker.UPDATER_RESTART_UNCONFIGURED)
    if tree_dirty is not False:  # unknown (not a git checkout) fails closed
        found.append(ApplyBlocker.TREE_DIRTY)
    return found


def _core_too_old(core_version: str, floor: str | None) -> bool:
    if floor is None:
        return False
    try:
        return SemanticVersion(core_version) < SemanticVersion(floor)
    except ValueError:  # an unreadable bound is never a silent "satisfied"
        return True


def _release_blockers(
    release: CoreRelease | None,
    installed_version: str,
    installed_lock_sha256: str | None,
    core_version: str,
) -> list[ApplyBlocker]:
    if release is None:
        return [ApplyBlocker.UNSIGNED_RELEASE]
    found: list[ApplyBlocker] = []
    if installed_lock_sha256 != release.lock_sha256:  # unreadable fails closed
        found.append(ApplyBlocker.NEEDS_ENVIRONMENT_UPDATE)
    if release.migrations == "breaking":
        found.append(ApplyBlocker.BREAKING_MIGRATION)
    if _major_differs(installed_version, release.version):
        found.append(ApplyBlocker.MAJOR_JUMP)
    if _core_too_old(core_version, release.min_core_version):
        found.append(ApplyBlocker.CORE_TOO_OLD)
    return found


def core_blockers(
    release: CoreRelease | None,
    *,
    config: UpdateApplyConfig,
    installed_version: str,
    installed_lock_sha256: str | None,
    tree_dirty: bool | None,
    install_method: InstallMethod,
    heartbeat: UpdaterHeartbeat | None,
    active_run: ApplyRun | None,
    core_version: str,
    now: datetime,
    environ: Mapping[str, str] | None = None,
    root: Path = Path("/"),
) -> list[ApplyBlocker]:
    """Every blocker of installing ``release`` on this host, in console order.

    Order: ``core_disabled``, ``not_host_install`` or
    ``core_install_unsupported`` (a host install other than a git checkout),
    ``apply_disabled``, ``updater_offline`` or ``updater_mismatch``,
    ``backup_unconfigured``, ``updater_restart_unconfigured`` (a core run
    ends with the updater restarting itself, so it needs the command),
    ``tree_dirty``, then for a verified release
    ``needs_environment_update`` (``uv.lock`` differs), ``breaking_migration``,
    ``major_jump`` and ``core_too_old`` (the installed public core
    ``core_version`` is older than the signed ``min_core_version``, or either
    does not parse) (``unsigned_release`` when there is no verified release),
    and ``run_active``. ``None`` for the tree state or the lock hash
    means unknown, which blocks. No overlay is required.
    """
    found = _host_blockers(
        config=config,
        installed_version=installed_version,
        tree_dirty=tree_dirty,
        install_method=install_method,
        heartbeat=heartbeat,
        core_version=core_version,
        now=now,
        environ=environ,
        root=root,
    )
    found += _release_blockers(
        release, installed_version, installed_lock_sha256, core_version
    )
    if active_run is not None:
        found.append(ApplyBlocker.RUN_ACTIVE)
    return found


__all__ = [
    "CORE_METHODS",
    "CORE_ONLY_BLOCKERS",
    "CoreRelease",
    "core_blockers",
    "tree_is_dirty",
]
