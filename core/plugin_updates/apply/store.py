"""File-based run store shared by the API and the updater (host installs only).

Layout under ``root``: ``runs/<id>.json`` (snapshot, atomic replace),
``runs/<id>.log.jsonl`` (append-only transitions), ``active/<plugin>`` (the
claim, holding the run id: one active run per plugin), ``locks/<plugin>.lock``
(an exclusive ``flock`` serialising every mutation of a plugin's runs across
threads and processes), ``expect/<plugin>.json``, ``auditing/<id>`` and
``audited/<id>`` (the outcome-audit claim and mark, :mod:`._store_audit`) and
``heartbeat.json``. Every file is written by atomic replace, so a reader never
sees a torn record. Finished runs are pruned after a retention period
(:meth:`RunStore.prune_finished`).
"""

from __future__ import annotations

import fcntl
import logging
import os
import secrets
from collections.abc import Collection, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ._store_audit import AuditMixin
from ._store_expect import ExpectationsMixin
from ._store_io import _PLUGIN, _RUN_ID, _atomic_write, _now, _read
from .models import (
    RELEASING_STATES,
    ApplyRun,
    RunKind,
    RunState,
    RunTransition,
)

logger = logging.getLogger(__name__)

#: Fields a transition may change; everything else on a run is immutable.
MUTABLE_FIELDS = frozenset(
    {
        "approved_by",
        "approval_expires_at",
        "failure",
        "must_stay_active",
        "previous_target",
        "target",
        "to_version",
        "tarball_sha256",
    }
)


class RunConflict(Exception):
    """Another run for the plugin is not finished."""

    def __init__(self, active_run_id: str) -> None:
        super().__init__(f"run {active_run_id} is active")
        self.active_run_id = active_run_id


class RunStateConflict(Exception):
    """The run is not in the state the caller expected (a concurrent change won)."""

    def __init__(self, run_id: str, actual: RunState) -> None:
        super().__init__(f"run {run_id} is {actual.value}")
        self.run_id = run_id
        self.actual = actual


class RunStore(ExpectationsMixin, AuditMixin):
    """Runs, claims, journal, heartbeat, expectations and audit marks on one filesystem."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        """The store directory."""
        return self._root

    @property
    def boot_dir(self) -> Path:
        """Directory the updater keeps boot-time evidence in."""
        return self._root / "boot"

    # -- paths and locking -------------------------------------------------

    def _run_path(self, run_id: str) -> Path:
        if not _RUN_ID.match(run_id):
            raise ValueError("invalid run id")
        return self._root / "runs" / f"{run_id}.json"

    def _log_path(self, run_id: str) -> Path:
        return self._run_path(run_id).with_name(f"{run_id}.log.jsonl")

    def _plugin(self, plugin: str) -> str:
        if not _PLUGIN.match(plugin):
            raise ValueError("invalid plugin name")
        return plugin

    def _claim_path(self, plugin: str) -> Path:
        return self._root / "active" / self._plugin(plugin)

    @contextmanager
    def _locked(self, plugin: str) -> Iterator[None]:
        path = self._root / "locks" / f"{self._plugin(plugin)}.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)  # closing releases the flock

    def plugin_lock(self, plugin: str) -> AbstractContextManager[None]:
        """The per-plugin run lock (blocking, exclusive, across processes).

        Held by the updater around staging, link switches and pruning. Not
        re-entrant: do not call :meth:`transition` (or any other locking
        method) for the same plugin while holding it.
        """
        return self._locked(plugin)

    # -- runs --------------------------------------------------------------

    def _live_holder(self, plugin: str) -> ApplyRun | None:
        holder = (_read(self._claim_path(plugin)) or "").strip()
        run = self.get(holder) if _RUN_ID.match(holder) else None
        return run if run is not None and run.state not in RELEASING_STATES else None

    def _holds_claim(self, run: ApplyRun) -> bool:
        holder = _read(self._claim_path(run.plugin))
        return holder is not None and holder.strip() == run.id

    def create(
        self,
        *,
        kind: RunKind,
        plugin: str,
        from_version: str | None,
        to_version: str | None,
        tarball_sha256: str | None,
        requested_by: str,
        approval_required: bool,
        approval_ttl_seconds: int,
        target: str | None = None,
        now: datetime | None = None,
    ) -> ApplyRun:
        """Claim ``plugin`` and write a new run; :class:`RunConflict` if claimed."""
        at = _now(now)
        run_id = f"pinstall-{at.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"
        state = RunState.AWAITING_APPROVAL if approval_required else RunState.APPROVED
        expires = (
            at + timedelta(seconds=approval_ttl_seconds) if approval_required else None
        )
        with self._locked(plugin):
            live = self._live_holder(plugin)
            if live is not None:
                raise RunConflict(live.id)
            run = ApplyRun(
                id=run_id,
                kind=kind,
                plugin=plugin,
                from_version=from_version,
                to_version=to_version,
                tarball_sha256=tarball_sha256,
                requested_by=requested_by,
                requested_at=at,
                state=state,
                target=target,
                updated_at=at,
                approval_expires_at=expires,
            )
            self._journal(run_id, RunTransition(at=at, state=state, actor=requested_by))
            self._save(run)
            _atomic_write(
                self._claim_path(plugin), run_id
            )  # last: makes the run visible
        return run

    def _save(self, run: ApplyRun) -> None:
        _atomic_write(self._run_path(run.id), run.model_dump_json())

    def _journal(self, run_id: str, item: RunTransition) -> None:
        path = self._log_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = ""
        try:
            with path.open("rb") as existing:
                existing.seek(0, os.SEEK_END)
                if existing.tell() > 0:
                    existing.seek(-1, os.SEEK_END)
                    if existing.read(1) != b"\n":
                        prefix = "\n"  # a torn line from a crash: do not glue onto it
        except OSError:
            pass
        with path.open("a", encoding="utf-8") as handle:
            handle.write(prefix + item.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def get(self, run_id: str) -> ApplyRun | None:
        """The run, or ``None`` when unknown or ``run_id`` is not a valid id."""
        try:
            text = _read(self._run_path(run_id))
            return None if text is None else ApplyRun.model_validate_json(text)
        except ValueError:
            return None

    def _transition_locked(
        self,
        run: ApplyRun,
        state: RunState,
        actor: str | None,
        message: str,
        at: datetime,
        fields: dict[str, object],
        expect: RunState | Collection[RunState] | None = None,
    ) -> ApplyRun:
        if run.state in RELEASING_STATES:
            raise RunStateConflict(run.id, run.state)  # terminal: never resurrected
        if expect is not None:
            allowed = {expect} if isinstance(expect, RunState) else set(expect)
            if run.state not in allowed:
                raise RunStateConflict(run.id, run.state)
        self._journal(
            run.id, RunTransition(at=at, state=state, actor=actor, message=message)
        )
        update = {"state": state, "message": message, "updated_at": at, **fields}
        updated = ApplyRun.model_validate({**run.model_dump(), **update})
        self._save(updated)
        if run.state is RunState.ROLLBACK_FAILED and state is not run.state:
            self._reopen_audit(run.id)  # its outcome changed: audit it again
        if state in RELEASING_STATES:
            claim = self._claim_path(run.plugin)
            if (_read(claim) or "").strip() == run.id:
                claim.unlink(missing_ok=True)
        return updated

    def transition(
        self,
        run_id: str,
        state: RunState,
        *,
        actor: str | None = None,
        message: str = "",
        now: datetime | None = None,
        expect: RunState | Collection[RunState] | None = None,
        **fields: object,
    ) -> ApplyRun:
        """Journal, then persist, ``state``; release the claim on a releasing state.

        ``expect`` is a compare-and-set on the current state, checked under the
        plugin lock; :class:`RunStateConflict` when it does not hold. A run in a
        releasing (terminal) state can never be transitioned again.
        """
        unknown = set(fields) - MUTABLE_FIELDS
        if unknown:
            raise ValueError(f"immutable or unknown run fields: {sorted(unknown)}")
        run = self.get(run_id)
        if run is None:
            raise ValueError(f"unknown run {run_id}")
        with self._locked(run.plugin):
            current = self.get(run_id)  # re-read under the lock
            if current is None:
                raise ValueError(f"unknown run {run_id}")
            return self._transition_locked(
                current, state, actor, message, _now(now), fields, expect
            )

    def _all(self) -> list[ApplyRun]:
        runs_dir = self._root / "runs"
        if not runs_dir.is_dir():
            return []
        found = [
            self.get(p.name.removesuffix(".json"))
            for p in runs_dir.glob("pinstall-*.json")
        ]
        return sorted(
            (r for r in found if r is not None), key=lambda r: (r.requested_at, r.id)
        )

    def runs(self, states: Collection[RunState] | None = None) -> list[ApplyRun]:
        """Every run of every plugin, oldest first (only ``states``, when given)."""
        found = self._all()
        return found if states is None else [r for r in found if r.state in states]

    def active(self, plugin: str) -> ApplyRun | None:
        """The run holding ``plugin``'s claim, unless it already finished."""
        return self._live_holder(plugin)

    def history(self, plugin: str, limit: int = 20) -> list[ApplyRun]:
        """Runs of ``plugin``, newest first."""
        self._plugin(plugin)
        return [r for r in reversed(self._all()) if r.plugin == plugin][:limit]

    def last_succeeded_update(self, plugin: str) -> ApplyRun | None:
        """The newest ``succeeded`` update of ``plugin``, however many runs followed."""
        self._plugin(plugin)
        return next(
            (
                r
                for r in reversed(self._all())
                if r.plugin == plugin
                and r.kind is RunKind.UPDATE
                and r.state is RunState.SUCCEEDED
            ),
            None,
        )

    def pending_approvals(self, now: datetime | None = None) -> list[ApplyRun]:
        """Runs still awaiting approval (expiring the stale ones first)."""
        self.expire_stale(now)
        return [r for r in self._all() if r.state is RunState.AWAITING_APPROVAL]

    def next_approved(self) -> ApplyRun | None:
        """The oldest approved run, for the updater to pick up."""
        return next(
            (
                r
                for r in self._all()
                if r.state is RunState.APPROVED and self._holds_claim(r)
            ),
            None,
        )

    def unfinished(self) -> list[ApplyRun]:
        """Runs that still hold their plugin's claim."""
        return [
            r
            for r in self._all()
            if r.state not in RELEASING_STATES and self._holds_claim(r)
        ]

    def journal(self, run_id: str) -> list[RunTransition]:
        """The transitions of a run, oldest first."""
        text = _read(self._log_path(run_id))
        if text is None:
            return []
        entries: list[RunTransition] = []
        for line in text.splitlines():
            if not line:
                continue
            try:
                entries.append(RunTransition.model_validate_json(line))
            except ValueError:
                logger.warning("plugin_update_journal_line_skipped run=%s", run_id)
        return entries

    def expire_stale(self, now: datetime | None = None) -> list[ApplyRun]:
        """Expire approval requests whose deadline passed and nobody answered."""
        at = _now(now)
        expired: list[ApplyRun] = []
        for run in self._all():
            if run.state is not RunState.AWAITING_APPROVAL:
                continue
            if run.approval_expires_at is None or run.approval_expires_at > at:
                continue
            with self._locked(run.plugin):
                current = self.get(run.id)
                if current is None or current.state is not RunState.AWAITING_APPROVAL:
                    continue  # answered while we waited for the lock
                expired.append(
                    self._transition_locked(
                        current, RunState.EXPIRED, "timeout", "", at, {}
                    )
                )
        return expired


__all__ = ["MUTABLE_FIELDS", "RunConflict", "RunStateConflict", "RunStore"]
