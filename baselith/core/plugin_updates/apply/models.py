"""Records of one-click plugin update runs, the updater heartbeat and restart expectations."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict

#: Claim and lock key of a core run. ``ApplyRun.plugin`` is the claim key and
#: matches the plugin-name pattern, so the core takes one reserved name that no
#: plugin run may use (:meth:`RunStore.create` refuses it for any other subject).
CORE_CLAIM: Final = "baselith_core"
#: ``ApplyRun.subject`` of a run that updates the core (the distribution).
CORE_SUBJECT: Final = "core"
#: Prefix of a plugin run's subject: ``plugin:<name>``.
PLUGIN_SUBJECT_PREFIX: Final = "plugin:"
#: The driver every run had before drivers existed: the host updater.
HOST_DRIVER: Final = "host"


class RunKind(StrEnum):
    """What a run does to the plugin."""

    UPDATE = "update"
    ROLLBACK = "rollback"


class RunState(StrEnum):
    """Lifecycle of a run."""

    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PREPARING = "preparing"
    MIGRATING = "migrating"
    ACTIVATING = "activating"
    HEALTH_CHECKING = "health_checking"
    ROLLING_BACK = "rolling_back"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"
    DENIED = "denied"
    EXPIRED = "expired"
    #: A pull request was opened and waits for review (GitOps driver).
    PR_OPEN = "pr_open"
    #: The pull request is approved and waits for someone to merge it.
    AWAITING_MERGE = "awaiting_merge"
    #: The merged change is rolling out to the deployment.
    ROLLING_OUT = "rolling_out"
    #: The new version serves; the updater restarts itself and closes the run.
    FINALIZING = "finalizing"


#: States that release the per-plugin claim. ``ROLLBACK_FAILED`` does not: the
#: plugin is in an unknown state and needs an operator before another run.
RELEASING_STATES = frozenset(
    {
        RunState.SUCCEEDED,
        RunState.FAILED,
        RunState.ROLLED_BACK,
        RunState.DENIED,
        RunState.EXPIRED,
    }
)


class RunTransition(BaseModel):
    """One journal entry."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    state: RunState
    actor: str | None = None
    message: str = ""


class ApplyRun(BaseModel):
    """Snapshot of a run. Never stores URLs."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: RunKind
    plugin: str
    from_version: str | None
    to_version: str | None
    tarball_sha256: str | None
    requested_by: str
    requested_at: datetime
    approved_by: str | None = None
    approval_expires_at: datetime | None = None
    state: RunState
    message: str = ""
    failure: str | None = None
    previous_target: str | None = None
    target: str | None = None
    #: Plugins active before the run's first restart (None until captured);
    #: every restart of the run, rollback included, must keep them active.
    must_stay_active: list[str] | None = None
    updated_at: datetime
    #: What the run updates: ``core`` or ``plugin:<name>``. ``None`` (every run
    #: file written before subjects existed) means ``plugin:<plugin>``.
    subject: str | None = None
    #: Which driver executes the run (``host``: the updater on this machine).
    driver: str = HOST_DRIVER

    @property
    def subject_key(self) -> str:
        """The subject, with ``None`` read as ``plugin:<plugin>``."""
        return self.subject or f"{PLUGIN_SUBJECT_PREFIX}{self.plugin}"


def subject_refusal(plugin: str, subject: str | None) -> str | None:
    """Why ``subject`` cannot be recorded for a run claiming ``plugin``, or None.

    The core uses the reserved claim :data:`CORE_CLAIM` and only it; a plugin
    run's subject is ``None`` or ``plugin:<plugin>``.
    """
    if subject == CORE_SUBJECT:
        return None if plugin == CORE_CLAIM else "the core subject needs the core claim"
    if plugin == CORE_CLAIM:
        return f"{CORE_CLAIM} is reserved for the core subject"
    if subject is None or subject == f"{PLUGIN_SUBJECT_PREFIX}{plugin}":
        return None
    return f"subject {subject!r} does not match plugin {plugin!r}"


class UpdaterHeartbeat(BaseModel):
    """Liveness and capabilities the updater publishes for the console."""

    model_config = ConfigDict(frozen=True)

    pid: int
    started_at: datetime
    at: datetime
    core_version: str
    enabled: bool
    overlay_root: str | None
    overlay_writable: bool
    restart_configured: bool
    #: The distribution version (``__version__``) the updater imported; a core
    #: install compares it with the API's. ``None`` in heartbeats written
    #: before it existed.
    framework_version: str | None = None


class Expectation(BaseModel):
    """What the API must report after the restart of a run."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    plugin: str
    version: str | None
    store_dir: str | None
    restart_at: datetime
    must_stay_active: list[str]


__all__ = [
    "CORE_CLAIM",
    "CORE_SUBJECT",
    "HOST_DRIVER",
    "PLUGIN_SUBJECT_PREFIX",
    "RELEASING_STATES",
    "ApplyRun",
    "Expectation",
    "RunKind",
    "RunState",
    "RunTransition",
    "UpdaterHeartbeat",
    "subject_refusal",
]
