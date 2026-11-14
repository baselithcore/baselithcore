"""Pre-flight items: the level, the record and the sentence of each blocker.

Split out of :mod:`.preflight` so the core subject's items
(:mod:`._core_preflight`) share them without an import cycle.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .models import ApplyBlocker


class PreflightLevel(StrEnum):
    """How much an item stands in the way.

    ``info`` is a fact, not a fault: the release does not install with one
    click here by design (a plugin that ships inside this tree arrives with
    the tree's next release), so there is nothing to fix, and the pre-flight
    is not ``ready`` either.
    """

    OK = "ok"
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"


class PreflightItem(BaseModel):
    """One finding of a pre-flight, with a sentence a person can act on."""

    model_config = ConfigDict(frozen=True)

    code: str
    level: PreflightLevel
    message: str


BLOCKER_TEXT: dict[ApplyBlocker, str] = {
    ApplyBlocker.APPLY_DISABLED: "One-click updates are off on this host.",
    ApplyBlocker.NOT_HOST_INSTALL: "This is not a host install (Kubernetes or a container).",
    ApplyBlocker.OVERLAY_UNCONFIGURED: "The updater has no writable overlay directory.",
    ApplyBlocker.UPDATER_OFFLINE: "The updater is not running (no recent heartbeat).",
    ApplyBlocker.UPDATER_MISMATCH: "The updater runs another core version than the API.",
    ApplyBlocker.UNSIGNED_RELEASE: "The release is not signed: follow the upgrade instructions.",
    ApplyBlocker.SIGNATURE_FAILED: "The release signature did not verify.",
    ApplyBlocker.NEEDS_ENVIRONMENT_UPDATE: "The release needs packages this environment lacks.",
    ApplyBlocker.HOST_BUILD_REQUIRED: "The release needs a host build.",
    ApplyBlocker.BACKUP_UNCONFIGURED: "No backup command is configured "
    "(UPDATE_APPLY_BACKUP_COMMAND); a core install needs one.",
    ApplyBlocker.UPDATER_RESTART_UNCONFIGURED: "No updater restart command is "
    "configured (UPDATE_APPLY_UPDATER_RESTART_COMMAND); a core install ends with "
    "the updater restarting itself.",
    ApplyBlocker.TREE_DIRTY: "The checkout has local changes to tracked files.",
    ApplyBlocker.BREAKING_MIGRATION: "The release has breaking schema migrations: "
    "upgrade by hand, following its release notes.",
    ApplyBlocker.MAJOR_JUMP: "The release crosses a major version: follow the "
    "upgrade instructions.",
    ApplyBlocker.CORE_DISABLED: "One-click core updates are off on this host "
    "(UPDATE_APPLY_CORE_ENABLED).",
    ApplyBlocker.CORE_INSTALL_UNSUPPORTED: "This host install is not a git "
    "checkout: the core is updated by hand here.",
    ApplyBlocker.CORE_TOO_OLD: "The installed core is older than the release "
    "allows to upgrade from: install an intermediate release first.",
    ApplyBlocker.RUN_ACTIVE: "Another run is in progress.",
}


def item(code: str, level: PreflightLevel, message: str) -> PreflightItem:
    """One pre-flight item."""
    return PreflightItem(code=code, level=level, message=message)


def block(code: str, message: str) -> PreflightItem:
    """A ``block`` item."""
    return item(code, PreflightLevel.BLOCK, message)


def warn(code: str, message: str) -> PreflightItem:
    """A ``warn`` item."""
    return item(code, PreflightLevel.WARN, message)


def info(code: str, message: str) -> PreflightItem:
    """An ``info`` item."""
    return item(code, PreflightLevel.INFO, message)


def in_tree_release_text(distribution: bool) -> str:
    """Why an unsigned release of a plugin shipped in this tree is no fault."""
    what = "distribution" if distribution else "core"
    return (
        f"This release arrives with the next {what} release; it has no signed "
        "artifact for a one-click install."
    )


__all__ = [
    "BLOCKER_TEXT",
    "PreflightItem",
    "PreflightLevel",
    "block",
    "in_tree_release_text",
    "info",
    "item",
    "warn",
]
