"""Install a signed core (distribution) release in place on a host git checkout.

The ``core-host`` driver (decision 5 of the update-process spec): the core is
a git checkout with an editable install, so a release is installed by
checking out the one commit its signed ``distribution-release.json`` names.
``execute`` journals every transition first (``expect=`` set where a
concurrent change could win):

1. ``preparing`` (expect ``approved``): the core switch, the kill switch, a
   restart command and a boot report must exist (``apply_disabled``);
2. an update fetches and verifies the manifest of ``to_version`` and holds it
   to the commit pinned at request time (``tarball_sha256`` carries it):
   a refusal fails with its code, another commit with ``commit_mismatch``;
3. the core guards run again, fresh (any blocker fails the run with its
   code); the schema owner's environment is read;
4. the backup command runs (``backup_failed``);
5. ``migrating`` records HEAD as ``previous_target`` (the rollback anchor),
   the pinned commit as ``target`` and the plugins that must stay active;
6. the commit is fetched by SHA and checked out detached
   (``checkout_failed``, HEAD unmoved); its ``uv.lock`` must hash as signed,
   or HEAD goes back and the run fails ``lock_mismatch``;
7. ``baselith db migrate`` then ``plugin schema-init`` run with the owner's
   credentials; a failure puts HEAD back without a restart
   (``migration_failed``: the running API never stopped);
8. ``activating``: the API restarts, ``health_checking`` judges it
   (:func:`._core_health.wait_core_healthy`); a failure checks out
   ``previous_target``, restarts again and ends ``rolled_back`` (or
   ``rollback_failed``, which keeps the claim);
9. success ends ``finalizing``: the updater still runs the old code and
   restarts itself; reconciliation at its next start closes the run, and one
   still open after ``UPDATE_APPLY_FINALIZE_TIMEOUT_SECONDS`` is closed by
   what the API serves (``reconcile.settle_finalizing``). The guards of step
   3 refuse a core run without ``UPDATE_APPLY_UPDATER_RESTART_COMMAND``
   (``updater_restart_unconfigured``).

A rollback run (``kind=rollback``) pins the commit to return to, skips the
manifest and the migrations (a rollback never reverts the schema) and
otherwise runs the same steps. Messages carry codes, versions, commit SHAs
and exit codes, never host paths or credentials. This module never imports
the ``plugins`` package.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from baselith.core.config.plugin_update_apply import UpdateApplyConfig

from ...upgrade_models import InstallMethod
from .._activation import NO_BOOT_REPORT
from .._io import CommandRunner, SchemaEnvError, schema_env
from ..boot_report import latest_active_plugins
from ..models import CORE_CLAIM, ApplyRun, RunKind, RunState, UpdaterHeartbeat
from ..store import RunStateConflict, RunStore
from ._core_guards import CoreRelease, core_blockers
from ._core_health import CoreExpectation, wait_core_healthy
from ._core_steps import (
    StepFailure,
    current_head,
    host_state,
    lock_matches,
    migrate,
    restore,
    run_backup,
    switch_to,
    verified_manifest,
)
from ._fetch import DistributionFetcher
from ._git import GitRunner

logger = logging.getLogger(__name__)

_SHA = re.compile(r"^[0-9a-f]{40}$")
FINALIZING_MESSAGE = "the new version serves; the updater restarts itself to finish"
_NO_RESTORE = "the previous commit could not be checked out again"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CoreHostDriver:
    """An :class:`~.base.ApplyDriver` for the ``core`` subject on a git checkout."""

    def __init__(
        self,
        *,
        store: RunStore,
        config: UpdateApplyConfig,
        git: GitRunner,
        runner: CommandRunner,
        fetcher: DistributionFetcher,
        probe: Callable[[], Awaitable[int]],
        trusted_keys: Callable[[str], list[str]],
        distribution_name: str,
        repo_root: Path,
        core_version: str,
        framework_version: str,
        install_method: InstallMethod = "source",
        remote_url: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """Wire the driver; ``now`` must be the wall clock workers stamp boot reports with."""
        self._store, self._config = store, config
        self._git, self._runner, self._fetcher = git, runner, fetcher
        self._probe, self._keys = probe, trusted_keys
        self._name, self._root = distribution_name, repo_root
        self._core, self._framework = core_version, framework_version
        self._method, self._remote = install_method, remote_url
        self._clock, self._sleep = clock, sleep
        self._now = now or _utcnow

    # -- small helpers --------------------------------------------------------

    async def _step(self, run: ApplyRun, state: RunState, **fields: Any) -> ApplyRun:
        return await asyncio.to_thread(self._store.transition, run.id, state, **fields)

    async def _fail(self, run: ApplyRun, code: str, detail: str = "") -> ApplyRun:
        logger.warning("AUDIT | CORE_UPDATE | failed run=%s code=%s", run.id, code)
        return await self._step(run, RunState.FAILED, message=detail, failure=code)

    def _disabled(self) -> str | None:
        if not self._config.core_enabled:
            return "core installs are off (UPDATE_APPLY_CORE_ENABLED)"
        if not self._config.enabled:
            return "the kill switch is off"
        if not self._config.restart_configured:
            return "no restart command configured"
        return None

    def _self_beat(self) -> UpdaterHeartbeat:
        at = self._now()
        return UpdaterHeartbeat(
            pid=os.getpid(),
            started_at=at,
            at=at,
            core_version=self._core,
            enabled=self._config.enabled,
            overlay_root=None,
            overlay_writable=False,
            restart_configured=self._config.restart_configured,
            framework_version=self._framework,
        )

    def _switch_locked(self, sha: str) -> StepFailure | None:
        with self._store.plugin_lock(CORE_CLAIM):
            return switch_to(self._git, self._root, self._remote, sha)

    def _restore_locked(self, sha: str | None) -> bool:
        with self._store.plugin_lock(CORE_CLAIM):
            return restore(self._git, self._root, sha)

    async def _blocked(
        self, run: ApplyRun, release: CoreRelease | None, target: str
    ) -> tuple[CoreRelease, StepFailure | None]:
        """The release judged against this host now (a rollback: same environment)."""
        lock, dirty = await asyncio.to_thread(
            host_state, self._git, self._root, self._config.ignored_paths
        )
        judged = release or CoreRelease(
            version=run.to_version or "",
            commit_sha=target,
            lock_sha256=lock or "",
            migrations="none",
        )
        blockers = core_blockers(
            judged,
            config=self._config,
            installed_version=self._framework,
            installed_lock_sha256=lock,
            tree_dirty=dirty,
            install_method=self._method,
            heartbeat=self._self_beat(),
            active_run=None,  # this run holds the claim
            core_version=self._core,
            now=self._now(),
        )
        if not blockers:
            return judged, None
        names = ", ".join(b.value for b in blockers)
        return judged, StepFailure(blockers[0].value, f"blocked: {names}")

    # -- entry points ---------------------------------------------------------

    async def execute(self, run: ApplyRun) -> ApplyRun:
        """Take an APPROVED core run to a terminal state, or ``finalizing``."""
        try:
            run = await self._step(run, RunState.PREPARING, expect=RunState.APPROVED)
        except RunStateConflict:  # someone else moved it: leave it alone
            return await asyncio.to_thread(self._store.get, run.id) or run
        disabled = self._disabled()
        if disabled is not None:
            return await self._fail(run, "apply_disabled", disabled)
        survivors = await asyncio.to_thread(latest_active_plugins, self._store)
        if survivors is None:  # fail closed: nothing known about what must survive
            return await self._fail(run, "apply_disabled", NO_BOOT_REPORT)
        target = (run.tarball_sha256 or "").lower()
        if not _SHA.fullmatch(target):
            return await self._fail(
                run, "verification_failed", "the run pins no commit"
            )
        release: CoreRelease | None = None
        if run.kind is RunKind.UPDATE:
            keys = await asyncio.to_thread(self._keys, self._name)
            found = await verified_manifest(
                self._fetcher,
                keys,
                name=self._name,
                version=run.to_version or "",
                pinned_commit=target,
            )
            if isinstance(found, StepFailure):
                return await self._fail(run, found.code, found.detail)
            release = CoreRelease.of(found)
        release, blocked = await self._blocked(run, release, target)
        if blocked is not None:
            return await self._fail(run, blocked.code, blocked.detail)
        env: dict[str, str] | None = None
        if run.kind is RunKind.UPDATE:
            try:  # refused before anything runs: never fall back to runtime creds
                env = await asyncio.to_thread(schema_env, self._config)
            except SchemaEnvError as exc:
                return await self._fail(run, "migration_failed", str(exc))
        backup = await asyncio.to_thread(run_backup, self._runner, self._config)
        if backup is not None:
            return await self._fail(run, backup.code, backup.detail)
        return await self._install(run, release, target, survivors, env)

    async def _install(
        self,
        run: ApplyRun,
        release: CoreRelease,
        target: str,
        survivors: list[str],
        env: dict[str, str] | None,
    ) -> ApplyRun:
        previous = await asyncio.to_thread(current_head, self._git, self._root)
        if previous is None:
            return await self._fail(run, "checkout_failed", "HEAD cannot be read")
        run = await self._step(
            run,
            RunState.MIGRATING,
            previous_target=previous,
            target=target,
            must_stay_active=survivors,
        )
        switched = await asyncio.to_thread(self._switch_locked, target)
        if switched is not None:
            return await self._undo(run, switched)
        if not await asyncio.to_thread(lock_matches, self._root, release.lock_sha256):
            failure = StepFailure(
                "lock_mismatch", "uv.lock differs from the signed hash"
            )
            return await self._undo(run, failure)
        if env is not None:
            migrated = await asyncio.to_thread(
                migrate, self._runner, env, schema_init=self._config.schema_init
            )
            if migrated is not None:
                return await self._undo(run, migrated)
        return await self._activate(run, release.version)

    async def _undo(self, run: ApplyRun, failure: StepFailure) -> ApplyRun:
        """Put HEAD back on ``previous_target`` without a restart (nothing stopped)."""
        if await asyncio.to_thread(self._restore_locked, run.previous_target):
            return await self._fail(run, failure.code, failure.detail)
        return await self._rollback_failed(
            run, failure.code, failure.detail, _NO_RESTORE
        )

    async def resume_activation(self, run: ApplyRun) -> ApplyRun:
        """Reconcile: the updater died while activating; check out, restart, judge again."""
        if run.target is None:
            return await self._roll_back(
                run, "interrupted", "the run recorded no commit"
            )
        switched = await asyncio.to_thread(self._switch_locked, run.target)
        if switched is not None:
            return await self._roll_back(run, switched.code, switched.detail)
        return await self._activate(run, run.to_version or "")

    async def redo_rollback(self, run: ApplyRun) -> ApplyRun:
        """Reconcile: the updater died while rolling back; do it again (idempotent)."""
        detail = run.message.partition("; rollback: ")[0]
        if detail.startswith("rollback: "):
            detail = ""
        return await self._roll_back(run, run.failure or "interrupted", detail)

    def undo_swap(self, run: ApplyRun) -> None:
        """Reconcile a ``migrating`` run: put HEAD back on ``previous_target``.

        Raises:
            ValueError: HEAD cannot be put back (reconciliation then ends the
                run ``rollback_failed``).
        """
        if run.previous_target is None:
            return  # nothing was switched yet
        if not self._restore_locked(run.previous_target):
            raise ValueError(_NO_RESTORE)

    # -- restart, verdict, rollback ------------------------------------------

    async def _restart_and_check(
        self, run: ApplyRun, version: str
    ) -> tuple[bool, str, str]:
        restart_at = self._now()
        rc = await asyncio.to_thread(
            self._runner,
            list(self._config.restart_command),
            timeout=float(self._config.restart_timeout_seconds),
        )
        if rc != 0:
            return False, "restart_failed", f"the restart command exited {rc}"
        if run.state is not RunState.ROLLING_BACK:  # a rollback stays ROLLING_BACK
            await self._step(run, RunState.HEALTH_CHECKING)
        expect = CoreExpectation(
            run_id=run.id,
            version=version,
            restart_at=restart_at,
            must_stay_active=list(run.must_stay_active or []),
        )
        verdict = await wait_core_healthy(
            self._store,
            expect,
            self._config,
            probe=self._probe,
            clock=self._clock,
            sleep=self._sleep,
        )
        return verdict.ok, "health_failed", verdict.reason

    async def _activate(self, run: ApplyRun, version: str) -> ApplyRun:
        run = await self._step(run, RunState.ACTIVATING)
        ok, code, detail = await self._restart_and_check(run, version)
        if not ok:
            current = await asyncio.to_thread(self._store.get, run.id)
            return await self._roll_back(current or run, code, detail)
        logger.info("AUDIT | CORE_UPDATE | serving run=%s version=%s", run.id, version)
        return await self._step(run, RunState.FINALIZING, message=FINALIZING_MESSAGE)

    def _rolled_back_note(self, run: ApplyRun, detail: str) -> str:
        if run.kind is not RunKind.UPDATE:
            return detail
        note = f"schema changes from {run.to_version} remain"
        return f"{detail}; {note}" if detail else note

    async def _roll_back(self, run: ApplyRun, code: str, detail: str) -> ApplyRun:
        """Check out ``previous_target`` and restart; ROLLBACK_FAILED keeps the claim."""
        run = await self._step(run, RunState.ROLLING_BACK, failure=code, message=detail)
        if not await asyncio.to_thread(self._restore_locked, run.previous_target):
            return await self._rollback_failed(run, code, detail, _NO_RESTORE)
        ok, _, why = await self._restart_and_check(run, run.from_version or "")
        if ok:
            return await self._step(
                run,
                RunState.ROLLED_BACK,
                message=self._rolled_back_note(run, detail),
                failure=code,
            )
        return await self._rollback_failed(run, code, detail, why)

    async def _rollback_failed(
        self, run: ApplyRun, code: str, detail: str, why: str
    ) -> ApplyRun:
        logger.critical("AUDIT | CORE_UPDATE | rollback_failed run=%s: %s", run.id, why)
        message = f"{detail}; rollback: {why}" if detail else f"rollback: {why}"
        return await self._step(
            run, RunState.ROLLBACK_FAILED, message=message, failure=code
        )


__all__ = ["FINALIZING_MESSAGE", "CoreHostDriver"]
