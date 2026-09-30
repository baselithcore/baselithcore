"""Records of one-click plugin update runs, the updater heartbeat and restart expectations."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


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
    "RELEASING_STATES",
    "ApplyRun",
    "Expectation",
    "RunKind",
    "RunState",
    "RunTransition",
    "UpdaterHeartbeat",
]
