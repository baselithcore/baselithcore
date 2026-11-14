"""The core subject's blocker items: the checkout, the backup, the signed release.

A core install has its own guards
(:func:`~.apply.drivers._core_guards.core_blockers`); this turns them into
pre-flight items with a sentence that says what is known. The state of the
host (installed version, ``uv.lock`` hash, whether the checkout is clean) and
the verified release are read elsewhere and passed in as a :class:`CoreState`;
a missing piece is "unknown", which blocks.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from baselith.core.config.plugin_update_apply import UpdateApplyConfig

from ._preflight_items import BLOCKER_TEXT, PreflightItem, block, warn
from .apply.drivers._core_guards import CoreRelease, core_blockers
from .apply.models import ApplyRun, UpdaterHeartbeat
from .models import ApplyBlocker
from .upgrade_models import InstallMethod


class CoreState(BaseModel):
    """What the host and the release say about a core install.

    ``installed_lock_sha256`` and ``tree_dirty`` are ``None`` when they could
    not be read (not a git checkout, no ``uv.lock``). ``release`` is the
    verified manifest of the target, ``None`` when it was refused or absent
    (``release_detail`` says why). ``release_checked`` is False when nobody
    fetched the manifest (a read-only pre-flight never contacts GitHub): the
    release's own blockers are then a ``warn``, ``release_unverified``,
    because the install request verifies it.
    """

    model_config = ConfigDict(frozen=True)

    installed_version: str
    installed_lock_sha256: str | None = None
    tree_dirty: bool | None = None
    release: CoreRelease | None = None
    release_detail: str = ""
    release_checked: bool = True


def _text(blocker: ApplyBlocker, state: CoreState) -> str:
    base = BLOCKER_TEXT.get(blocker, blocker.value)
    if blocker is ApplyBlocker.TREE_DIRTY and state.tree_dirty is None:
        return "The checkout's state could not be read (is this a git checkout?)."
    if blocker is ApplyBlocker.NEEDS_ENVIRONMENT_UPDATE:
        if state.installed_lock_sha256 is None:
            return (
                "The installed uv.lock could not be read, so the release's "
                "dependencies cannot be compared with this environment."
            )
        return (
            "The release changes uv.lock: update the environment by hand "
            "(uv sync --frozen at the release commit)."
        )
    if blocker is ApplyBlocker.UNSIGNED_RELEASE:
        detail = f" ({state.release_detail})" if state.release_detail else ""
        return (
            "The release carries no verified distribution-release.json"
            f"{detail}: follow the upgrade instructions."
        )
    return base


def core_blocker_items(
    state: CoreState,
    *,
    config: UpdateApplyConfig,
    method: InstallMethod,
    heartbeat: UpdaterHeartbeat | None,
    active_run: ApplyRun | None,
    core_version: str,
    now: datetime,
) -> list[PreflightItem]:
    """One ``blocker_<code>`` item per core blocker, in console order."""
    blockers = core_blockers(
        state.release,
        config=config,
        installed_version=state.installed_version,
        installed_lock_sha256=state.installed_lock_sha256,
        tree_dirty=state.tree_dirty,
        install_method=method,
        heartbeat=heartbeat,
        active_run=active_run,
        core_version=core_version,
        now=now,
    )
    items = [
        block(f"blocker_{b.value}", _text(b, state))
        for b in blockers
        if state.release_checked or b is not ApplyBlocker.UNSIGNED_RELEASE
    ]
    if not state.release_checked:
        items.append(
            warn(
                "release_unverified",
                "The release's signed distribution-release.json is checked when "
                "the install is requested (this pre-flight does not contact GitHub).",
            )
        )
    return items


__all__ = ["CoreState", "core_blocker_items"]
