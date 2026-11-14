"""The auto-apply policy: pre-approved patch runs inside the maintenance window.

The updater calls :meth:`PolicyRunner.tick` every ``UPDATE_APPLY_POLICY_TICK_SECONDS``
before it looks for approved runs. A tick creates at most one run, through
:meth:`RunStore.create` exactly as a manual request would (pinned to the signed
tarball, or to the commit a verified distribution manifest names), with
``requested_by="policy:auto-patch"`` and no approval step: the policy *is* the
approval, given by two administrators (:mod:`baselith.core.plugin_updates.policy`).

The rules, every one failing closed:

- nothing created outside the window, with the policy off, or without a
  cached report; the updater checks the window again before it **starts** a
  policy run (:mod:`._policy_gate`), so a run that waited behind another one
  past the window is deferred to the next window instead of starting;
- patch releases only (:func:`._bump.bump_kind`): never a minor, a major, a
  pre-release or a version that does not parse;
- signed releases only, never a refused one, never one that needs a host build,
  never one with a ``block`` pre-flight item, never one already tried (a run
  of the same version that succeeded, failed or rolled back);
- plugins first, alphabetically, then the core, and the core only while no
  plugin run is unfinished, only when its verified manifest says
  ``migrations: none`` and its ``uv.lock`` equals the installed one;
- a subject whose estimated duration does not fit in what is left of the
  window waits for the next window (a run already started always finishes);
- a busy or unreadable task queue defers the run (``update.policy_deferred``).

Two updaters, or a manual request racing the tick, cannot create two runs for
one subject: the store's claim refuses the second (``RunConflict``, swallowed).
"""

from __future__ import annotations

import asyncio
import functools
import logging
import statistics
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Protocol

from baselith.core.config.plugin_update_apply import UpdateApplyConfig

from .._core_preflight import CoreState
from ..models import CheckReport, UpdateCandidate
from ..policy import MaintenanceWindow, UpdatePolicy
from ..preflight import PreflightItem, PreflightLevel
from ..window import in_window, remaining
from ._bump import bump_kind
from ._events import POLICY_APPLIED_EVENT, POLICY_DEFERRED_EVENT
from ._store_io import _PLUGIN
from .models import (
    CORE_CLAIM,
    CORE_SUBJECT,
    PLUGIN_SUBJECT_PREFIX,
    RunKind,
    RunState,
)
from .store import RunConflict, RunStore

logger = logging.getLogger(__name__)

#: ``requested_by`` of every run the policy creates.
POLICY_ACTOR: Final = "policy:auto-patch"
#: Run states that never executed: they do not count as a try of a release.
_NOT_TRIED = frozenset({RunState.DENIED, RunState.EXPIRED})

Emit = Callable[[str, dict[str, object]], Awaitable[bool]]
Preflight = Callable[[str, str, CoreState | None], Sequence[PreflightItem]]


class ReportSource(Protocol):
    """Where the last update report is read from (:class:`~..cache.UpdateCache`)."""

    def load(self) -> CheckReport | None: ...


@dataclass(frozen=True)
class _Pick:
    subject: str
    claim: str
    from_version: str
    to_version: str
    pin: str
    core: CoreState | None = None


def _claim_of(subject: str) -> str:
    return (
        CORE_CLAIM
        if subject == CORE_SUBJECT
        else subject.removeprefix(PLUGIN_SUBJECT_PREFIX)
    )


def journal_estimate(store: RunStore, subject: str, *, fallback: int) -> int:
    """Median seconds of the last five succeeded updates of ``subject``.

    Measured from the run's first ``preparing`` transition (its start) to the
    run's last update (its end); ``fallback`` when none is on record.
    """
    claim = _claim_of(subject)
    if not _PLUGIN.match(claim):
        return fallback
    done = [
        r
        for r in store.history(claim, limit=50)
        if r.kind is RunKind.UPDATE and r.state is RunState.SUCCEEDED
    ][:5]
    durations: list[float] = []
    for run in done:
        started = next(
            (t.at for t in store.journal(run.id) if t.state is RunState.PREPARING),
            run.requested_at,
        )
        durations.append(max((run.updated_at - started).total_seconds(), 1.0))
    return int(statistics.median(durations)) if durations else fallback


class PolicyRunner:
    """Turns the policy and the cached report into at most one run per tick."""

    def __init__(
        self,
        *,
        store: RunStore,
        config: UpdateApplyConfig,
        cache: ReportSource,
        queue_probe: Callable[[], int],
        estimate: Callable[[str], int],
        now: Callable[[], datetime],
        emit: Emit,
        preflight: Preflight,
        core_state: Callable[[str], Awaitable[CoreState]],
        installed_core: str,
        distribution: bool,
    ) -> None:
        self._store = store
        self._config = config
        self._cache = cache
        self._queue_probe = queue_probe
        self._estimate = estimate
        self._now = now
        self._emit = emit
        self._preflight = preflight
        self._core_state = core_state
        self._installed_core = installed_core
        self._distribution = distribution

    async def tick(self) -> list[str]:
        """Create the next due run; the ids created (zero or one)."""
        policy = await asyncio.to_thread(self._store.get_policy)
        window = policy.window
        if not policy.enabled or window is None:
            return []
        at = self._now()
        if not in_window(window, at):
            return []
        found = await asyncio.to_thread(self._cache.load)
        if found is None:
            return []
        pick = await self._choose(policy, found, window, at)
        if pick is None or not await self._queue_idle(pick.subject):
            return []
        return await self._create(pick)

    # -- choosing ------------------------------------------------------------

    async def _choose(
        self,
        policy: UpdatePolicy,
        found: CheckReport,
        window: MaintenanceWindow,
        at: datetime,
    ) -> _Pick | None:
        for candidate in sorted(found.candidates, key=lambda c: c.plugin):
            pick = await asyncio.to_thread(self._plugin_pick, policy, candidate)
            if pick is not None and await self._clear(pick, window, at):
                return pick
        if policy.core != "patch" or await asyncio.to_thread(self._plugins_busy):
            return None
        pick = await self._core_pick(found)
        if pick is not None and await self._clear(pick, window, at):
            return pick
        return None

    def _tried(self, claim: str, version: str) -> bool:
        return any(
            r.kind is RunKind.UPDATE
            and r.to_version == version
            and r.state not in _NOT_TRIED
            for r in self._store.history(claim, limit=50)
        )

    def _plugins_busy(self) -> bool:
        return any(r.plugin != CORE_CLAIM for r in self._store.unfinished())

    def _plugin_pick(self, policy: UpdatePolicy, c: UpdateCandidate) -> _Pick | None:
        assets = c.signed_assets
        if (
            not _PLUGIN.match(c.plugin)
            or c.plugin == CORE_CLAIM
            or not policy.allows_plugin(c.plugin)
            or not c.available
            or c.refusal is not None
            or c.latest is None
            or c.installed_version is None
            or assets is None
            or not assets.verified
            or assets.refusal is not None
            or assets.host_build_required
            or not assets.tarball_sha256
        ):
            return None
        target = c.latest.version
        if bump_kind(c.installed_version, target) != "patch":
            return None
        if self._store.active(c.plugin) is not None or self._tried(c.plugin, target):
            return None
        return _Pick(
            subject=f"{PLUGIN_SUBJECT_PREFIX}{c.plugin}",
            claim=c.plugin,
            from_version=c.installed_version,
            to_version=target,
            pin=assets.tarball_sha256,
        )

    async def _core_pick(self, found: CheckReport) -> _Pick | None:
        notice = found.distribution if self._distribution else found.system
        if notice is None or not notice.available or notice.latest is None:
            return None
        target = notice.latest.version
        if bump_kind(self._installed_core, target) != "patch":
            return None
        busy = await asyncio.to_thread(self._store.active, CORE_CLAIM)
        if busy is not None or await asyncio.to_thread(self._tried, CORE_CLAIM, target):
            return None
        try:
            state = await self._core_state(target)
        except Exception as exc:  # unverifiable now: try again next tick
            logger.info("update_policy_core_unverified error=%s", type(exc).__name__)
            return None
        release = state.release
        if (
            release is None
            or release.version != target
            or release.migrations != "none"
            or state.installed_lock_sha256 is None
            or state.installed_lock_sha256 != release.lock_sha256
        ):
            return None
        return _Pick(
            subject=CORE_SUBJECT,
            claim=CORE_CLAIM,
            from_version=self._installed_core,
            to_version=target,
            pin=release.commit_sha,
            core=state,
        )

    async def _clear(
        self, pick: _Pick, window: MaintenanceWindow, at: datetime
    ) -> bool:
        """No pre-flight block, and the run fits in what is left of the window."""
        try:
            seconds = self._estimate(pick.subject)
        except Exception as exc:  # an unreadable history: assume the default
            logger.info("update_policy_estimate_failed error=%s", type(exc).__name__)
            seconds = self._config.policy_estimate_seconds
        if timedelta(seconds=seconds) > remaining(window, at):
            await self._defer("window_too_short", pick.subject)
            return False
        try:
            items = await asyncio.to_thread(
                self._preflight, pick.subject, pick.to_version, pick.core
            )
        except Exception as exc:  # an unknown verdict never applies
            logger.info(
                "update_policy_preflight_failed subject=%s error=%s",
                pick.subject,
                type(exc).__name__,
            )
            return False
        return not any(i.level is PreflightLevel.BLOCK for i in items)

    # -- acting ----------------------------------------------------------------

    async def _defer(self, code: str, subject: str) -> None:
        logger.info("update_policy_deferred code=%s subject=%s", code, subject)
        await self._emit(POLICY_DEFERRED_EVENT, {"code": code, "subject": subject})

    async def _queue_idle(self, subject: str) -> bool:
        try:
            jobs = await asyncio.to_thread(self._queue_probe)
        except Exception:
            await self._defer("probe_failed", subject)
            return False
        if jobs > 0:
            await self._defer("queue_busy", subject)
            return False
        return True

    async def _create(self, pick: _Pick) -> list[str]:
        create = functools.partial(
            self._store.create,
            kind=RunKind.UPDATE,
            plugin=pick.claim,
            subject=CORE_SUBJECT if pick.claim == CORE_CLAIM else None,
            from_version=pick.from_version,
            to_version=pick.to_version,
            tarball_sha256=pick.pin,
            requested_by=POLICY_ACTOR,
            approval_required=False,
            approval_ttl_seconds=self._config.approval_ttl_seconds,
        )
        try:
            run = await asyncio.to_thread(create)
        except RunConflict:  # another updater or a manual request won the claim
            logger.info("update_policy_conflict subject=%s", pick.subject)
            return []
        except ValueError as exc:
            logger.warning(
                "update_policy_refused subject=%s error=%s", pick.subject, exc
            )
            return []
        logger.warning(
            "AUDIT | PLUGIN_UPDATE | policy request run=%s plugin=%s by=%s",
            run.id,
            pick.claim,
            POLICY_ACTOR,
        )
        await self._emit(
            POLICY_APPLIED_EVENT,
            {
                "subject": pick.subject,
                "from_version": pick.from_version,
                "to_version": pick.to_version,
                "run_id": run.id,
            },
        )
        return [run.id]


__all__ = ["POLICY_ACTOR", "PolicyRunner", "ReportSource", "journal_estimate"]
