"""Typed failures of the checkpoint concurrency contract.

Split from :mod:`baselith.core.orchestration.checkpoint` (module size cap) and
re-exported there. Kept free of imports so every store backend can raise them
without pulling in the replay manager.

Two different races, two different errors:

* :class:`CheckpointConflictError` — a **write** lost the compare-and-swap on
  ``version``: someone else wrote the run since this copy was loaded. The
  writer holds stale state, so the only safe move is to stop; retrying the
  write would clobber the newer state with the older one.
* :class:`CheckpointLeaseHeldError` — a **claim** found the run owned by
  another live executor (its lease has not expired). Nothing was written;
  the caller simply does not get to run it now.
"""

from __future__ import annotations


class CheckpointConflictError(RuntimeError):
    """A checkpoint write lost the optimistic-concurrency check.

    Attributes:
        run_id: The run whose write was refused.
        expected_version: The version the writer believed was current.
    """

    def __init__(self, run_id: str, expected_version: int | None = None) -> None:
        self.run_id = run_id
        self.expected_version = expected_version
        detail = (
            f" (expected version {expected_version})"
            if expected_version is not None
            else ""
        )
        super().__init__(
            f"checkpoint {run_id!r} was written by another executor{detail}; "
            "this copy is stale and the run stops here"
        )


class CheckpointLeaseHeldError(RuntimeError):
    """A run could not be claimed because another executor holds its lease.

    Attributes:
        run_id: The run that was asked for.
        owner: The executor currently holding the lease, when known.
        lease_expires_at: Unix time the holder's lease runs out, when known.
    """

    def __init__(
        self,
        run_id: str,
        owner: str | None = None,
        lease_expires_at: float | None = None,
    ) -> None:
        self.run_id = run_id
        self.owner = owner
        self.lease_expires_at = lease_expires_at
        super().__init__(
            f"checkpoint {run_id!r} is leased by another executor ({owner})"
        )


__all__ = ["CheckpointConflictError", "CheckpointLeaseHeldError"]
