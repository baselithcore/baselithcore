"""Per-run execution leases for durable checkpoints.

Before this module, "who is executing this run?" was guessed from silence: the
recovery sweep re-entered a ``running`` checkpoint once nothing had been
written for ``recovery_resume_after_seconds``. A single LLM or tool call longer
than that window made a live run look abandoned, and a second worker resumed
it — the same agent loop twice, with duplicated side effects and spend.

A lease makes ownership explicit. Every attempt at a run gets a unique
``owner`` id and holds ``owner`` + ``lease_expires_at`` on the checkpoint:

* **claim** — a conditional update that succeeds only when the run is
  unowned, its lease has expired, or the caller already owns it. Taking over
  from another owner also bumps ``version``, so any write still in flight from
  the previous owner loses its compare-and-swap instead of clobbering the
  new owner's state.
* **renew** — every ``run_step`` save carries a fresh expiry, and a heartbeat
  task renews it every third of the lease while the attempt runs, so a long
  call never looks abandoned. Renewal never touches ``updated_at``: the
  stale-run sweep still sees a wedged loop as silent.
* **release** — the attempt hands the run back when it ends (completed,
  failed, paused or cancelled).

The manager-side half lives here as :class:`LeaseHolder`, mixed into
:class:`~baselith.core.orchestration.checkpoint.CheckpointManager`; the store
half is three optional methods (``claim`` / ``renew_lease`` /
``release_lease``) that every shipped backend implements. A store without them
keeps working, without leases.
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
import uuid
from typing import TYPE_CHECKING, Any

from baselith.core.observability.logging import get_logger
from baselith.core.orchestration.checkpoint_errors import CheckpointConflictError

if TYPE_CHECKING:
    from baselith.core.orchestration.checkpoint import Checkpoint

logger = get_logger(__name__)

#: Fallback lease length when the orchestration settings cannot be read.
DEFAULT_LEASE_SECONDS = 60.0

#: ``plugin_data`` marker of a run its executor handed back on a drain/SIGTERM
#: shutdown: ``running``, unowned, and resumable by the next sweep at once
#: (no silence wait). Cleared when the run is resumed.
YIELDED_KEY = "yielded_for_shutdown"

#: Store methods that make up the lease contract.
_LEASE_METHODS = ("claim", "renew_lease", "release_lease")


def new_owner_id() -> str:
    """A fresh owner id for one attempt at a run: ``host:pid:random``.

    Per attempt, not per process: two resumes of the same run racing inside
    one worker must exclude each other exactly like two replicas do.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"


def supports_leases(store: Any) -> bool:
    """Whether ``store`` implements the optional lease methods."""
    return all(callable(getattr(store, name, None)) for name in _LEASE_METHODS)


def configured_lease_seconds() -> float:
    """``ORCHESTRATOR_CHECKPOINT_LEASE_SECONDS``, or the default on error."""
    try:
        from baselith.core.config.orchestration import get_orchestration_config

        return float(get_orchestration_config().checkpoint_lease_seconds)
    except Exception:  # pragma: no cover - config must not break a run
        logger.debug("checkpoint_lease_seconds_unavailable", exc_info=True)
        return DEFAULT_LEASE_SECONDS


def was_yielded(checkpoint: Any) -> bool:
    """True for a run handed back by a shutdown (see :data:`YIELDED_KEY`)."""
    plugin_data = getattr(checkpoint, "plugin_data", None) or {}
    return bool(plugin_data.get(YIELDED_KEY))


def lease_is_live(checkpoint: Any, now: float | None = None) -> bool:
    """True when the checkpoint carries an owner whose lease has not expired."""
    expires = getattr(checkpoint, "lease_expires_at", None)
    if getattr(checkpoint, "owner", None) is None or expires is None:
        return False
    current = time.time() if now is None else now
    return float(expires) > current


class LeaseHolder:
    """Lease bookkeeping for the replay manager (see module docstring).

    ``owner`` is ``None`` when the store has no lease support; every method
    is then a no-op, so a protocol-only store behaves exactly as before.
    """

    store: Any
    checkpoint: Checkpoint
    owner: str | None
    lease_seconds: float
    _lease_lost: bool
    _heartbeat: asyncio.Task[None] | None

    def _init_lease(self, owner: str | None, lease_seconds: float | None) -> None:
        self.owner = owner
        self.lease_seconds = (
            float(lease_seconds) if lease_seconds else DEFAULT_LEASE_SECONDS
        )
        self._lease_lost = False
        self._heartbeat = None

    @property
    def lease_lost(self) -> bool:
        """True once a renewal found the run owned by someone else."""
        return self._lease_lost

    def extend_local_lease(self, now: float | None = None) -> None:
        """Stamp a fresh expiry on the checkpoint; the next save persists it."""
        if self.owner is not None and not self._lease_lost:
            current = time.time() if now is None else now
            self.checkpoint.lease_expires_at = current + self.lease_seconds

    async def renew_lease(self) -> bool:
        """Extend the lease in the store; False once ownership is gone."""
        if self.owner is None:
            return True
        if self._lease_lost:
            return False
        now = time.time()
        renewed = bool(
            await self.store.renew_lease(
                self.checkpoint.run_id, self.owner, self.lease_seconds, now=now
            )
        )
        if renewed:
            self.checkpoint.lease_expires_at = now + self.lease_seconds
        else:
            self._lease_lost = True
            logger.warning(
                "checkpoint_lease_lost run=%s owner=%s",
                self.checkpoint.run_id,
                self.owner,
            )
        return renewed

    async def ensure_lease(self) -> None:
        """Fence a side effect: prove ownership before it runs.

        Cheap in the common case — the heartbeat keeps the local expiry well
        ahead, so this is a clock read. Below half a lease left it renews
        synchronously; a run whose ownership cannot be proven does not get to
        execute the next tool.

        Raises:
            CheckpointConflictError: The lease belongs to another executor.
        """
        if self.owner is None:
            return
        if not self._lease_lost:
            remaining = (self.checkpoint.lease_expires_at or 0.0) - time.time()
            if remaining > self.lease_seconds / 2:
                return
            if await self.renew_lease():
                return
        raise CheckpointConflictError(self.checkpoint.run_id, self.checkpoint.version)

    def start_heartbeat(self) -> None:
        """Renew the lease every third of its length until :meth:`end_attempt`."""
        if self.owner is None or self._heartbeat is not None:
            return
        self._heartbeat = asyncio.create_task(
            self._heartbeat_loop(), name=f"checkpoint-lease:{self.checkpoint.run_id}"
        )

    async def _heartbeat_loop(self) -> None:
        interval = max(self.lease_seconds / 3.0, 0.01)
        while not self._lease_lost:
            await asyncio.sleep(interval)
            try:
                await self.renew_lease()
            except Exception as exc:
                # A store blip must not kill the heartbeat: the next tick
                # retries, and ensure_lease() fences side effects meanwhile.
                logger.warning(
                    "checkpoint_lease_renew_failed run=%s error=%s",
                    self.checkpoint.run_id,
                    exc,
                )

    async def end_attempt(self) -> None:
        """Stop the heartbeat and release the lease (owner-conditional)."""
        task, self._heartbeat = self._heartbeat, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # pragma: no cover - loop catches its own
                logger.debug("checkpoint_heartbeat_exit error=%s", exc)
        if self.owner is None or self._lease_lost:
            return
        await self.store.release_lease(self.checkpoint.run_id, self.owner)
        self.checkpoint.lease_expires_at = None


async def end_checkpoint_attempt(manager: Any) -> None:
    """End one attempt for whatever sits in ``context["checkpoint"]``.

    Shielded so a cancellation wave cannot strand the lease mid-release (it
    would then block recovery for one lease length). Failures are logged:
    an unreleased lease simply expires.
    """
    end = getattr(manager, "end_attempt", None)
    if end is None:
        return
    try:
        await asyncio.shield(end())
    except Exception as exc:
        logger.warning("checkpoint_lease_release_failed error=%s", exc)


__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "YIELDED_KEY",
    "LeaseHolder",
    "configured_lease_seconds",
    "end_checkpoint_attempt",
    "lease_is_live",
    "new_owner_id",
    "supports_leases",
    "was_yielded",
]
