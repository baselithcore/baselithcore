"""The run store's outcome-audit markers and retention (split for the size cap).

A finished run's outcome is audited once by whoever consumes the store (a
console, a compliance exporter). Several web workers may try at once, so an
auditor first *claims* the run (``auditing/<id>``, created under a store-wide
``flock``), writes its audit event, then *marks* it (``audited/<id>``,
``O_EXCL``) — or releases the claim when the write failed, so the next pass
retries. A claim older than :data:`AUDIT_LEASE` belongs to a worker that died
and is taken over.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from ._store_io import _RUN_ID, _atomic_write, _now
from .models import RELEASING_STATES, ApplyRun, RunKind, RunState

#: A claim older than this was left by a worker that died: it is taken over.
AUDIT_LEASE = timedelta(minutes=5)
#: Finished, audited runs older than this are pruned.
RETENTION = timedelta(days=90)
#: Finished runs nobody ever audited are kept this long before pruning.
UNAUDITED_RETENTION = timedelta(days=365)


class AuditMixin:
    """Audit claims, audit marks and retention of :class:`~.store.RunStore`."""

    _root: Path

    if TYPE_CHECKING:

        def _run_path(self, run_id: str) -> Path: ...

        def _log_path(self, run_id: str) -> Path: ...

        def _all(self) -> list[ApplyRun]: ...

        def _locked(self, plugin: str) -> AbstractContextManager[None]: ...

        def get(self, run_id: str) -> ApplyRun | None: ...

    def _marker(self, kind: str, run_id: str) -> Path:
        self._run_path(run_id)  # validates the id
        return self._root / kind / run_id

    @contextmanager
    def _audit_lock(self) -> Iterator[None]:
        path = self._root / "locks" / ".audit.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def claim_audit(self, run_id: str, now: datetime | None = None) -> bool:
        """Claim the right to write ``run_id``'s outcome audit now.

        False when the run is already audited or another auditor claimed it
        less than :data:`AUDIT_LEASE` ago. The winner writes its audit event,
        then calls :meth:`mark_audited` — or :meth:`release_audit` when the
        write failed.
        """
        pending = self._marker("auditing", run_id)
        at = _now(now).timestamp()
        with self._audit_lock():
            if self.is_audited(run_id):
                return False
            try:
                claimed_at = pending.stat().st_mtime
            except FileNotFoundError:
                claimed_at = None
            if claimed_at is not None and at - claimed_at < AUDIT_LEASE.total_seconds():
                return False
            _atomic_write(pending, str(os.getpid()))
            os.utime(pending, (at, at))
        return True

    def release_audit(self, run_id: str) -> None:
        """Drop a claim whose audit write failed, so the next pass retries."""
        self._marker("auditing", run_id).unlink(missing_ok=True)

    def mark_audited(self, run_id: str) -> bool:
        """True exactly once per run: its outcome audit is written. Ends any claim."""
        path = self._marker("audited", run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError:
            return False
        finally:
            self.release_audit(run_id)
        return True

    def is_audited(self, run_id: str) -> bool:
        """Whether :meth:`mark_audited` already recorded ``run_id``."""
        return _RUN_ID.match(run_id) is not None and (
            (self._root / "audited" / run_id).exists()
        )

    def _reopen_audit(self, run_id: str) -> None:
        """Forget that ``run_id`` was audited: its outcome changed after the audit."""
        self._marker("audited", run_id).unlink(missing_ok=True)
        self.release_audit(run_id)

    # -- retention ----------------------------------------------------------

    def prune_finished(
        self, now: datetime | None = None, *, older_than: timedelta = RETENTION
    ) -> list[str]:
        """Delete old finished runs (snapshot, journal, markers); the ids removed.

        A run goes once it released its claim and was last updated more than
        ``older_than`` ago *and* is audited — or more than
        :data:`UNAUDITED_RETENTION` ago when nobody audits this store. The
        newest succeeded update of each plugin is always kept: a roll back
        needs it. ``rollback_failed`` runs are never pruned.
        """
        at = _now(now)
        runs = self._all()
        newest: dict[str, str] = {}  # plugin -> its newest succeeded update
        for run in runs:  # oldest first: later ones overwrite
            if run.kind is RunKind.UPDATE and run.state is RunState.SUCCEEDED:
                newest[run.plugin] = run.id
        keep = set(newest.values())
        removed: list[str] = []
        for run in runs:
            if run.state not in RELEASING_STATES or run.id in keep:
                continue
            age = at - run.updated_at
            limit = older_than if self.is_audited(run.id) else UNAUDITED_RETENTION
            if age <= limit:
                continue
            with self._locked(run.plugin):
                self._run_path(run.id).unlink(missing_ok=True)
                self._log_path(run.id).unlink(missing_ok=True)
                self._marker("audited", run.id).unlink(missing_ok=True)
                self.release_audit(run.id)
            removed.append(run.id)
        return removed


__all__ = ["AUDIT_LEASE", "RETENTION", "UNAUDITED_RETENTION", "AuditMixin"]
