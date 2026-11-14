"""Crash reconciliation: finish or undo what a dead updater left.

Runs when the updater starts, before it takes new work, and again whenever an
executor call raised unexpectedly, so no run is stranded holding its plugin's
claim. Per non-terminal state:

========================  ==================================================
``awaiting_approval``     expired once past its deadline
``approved``              untouched (the loop runs it normally)
``preparing``             nothing was switched: staging removed, ``failed``
``migrating``             ``schema-init`` ran against a scratch overlay and
                          the live link was not switched yet: scratch
                          removed, ``failed`` (a link an older updater had
                          already switched is put back on ``previous_target``)
``activating``            switched (again, idempotently) and restarted, then
                          checked (``resume_activation``): the crash may have
                          come before or after the switch
``health_checking``       same as ``activating``
``rolling_back``          the rollback is redone (``redo_rollback``)
``finalizing``            a core run whose new version serves: ``succeeded``
                          once this updater imported that version and the
                          newest boot report shows it; past
                          ``finalize_timeout_seconds`` closed anyway
                          (:func:`settle_finalizing`), otherwise left alone
                          (``updater_mismatch`` explains it)
``rollback_failed``       untouched: an operator resolves it
========================  ==================================================

Every interrupted run fails with the code ``interrupted``. Expectations of
finished runs are dropped last. All blocking work runs in worker threads.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from baselith.core.plugins.overlay import STORE_DIRNAME

from ._events import announce_rollback_failed
from .boot_report import latest_framework_version
from .models import ApplyRun, RunState
from .store import RunStateConflict, RunStore

logger = logging.getLogger(__name__)

INTERRUPTED = "interrupted"
#: Failure of a ``finalizing`` run whose target no longer serves at the timeout.
FINALIZE_TIMEOUT = "finalize_timeout"
_UNDO_REFUSED = "the plugin's overlay link could not be switched back"


class RecoveringExecutor(Protocol):
    """The executor entry points reconciliation drives."""

    async def resume_activation(self, run: ApplyRun) -> ApplyRun: ...

    async def redo_rollback(self, run: ApplyRun) -> ApplyRun: ...

    def undo_swap(self, run: ApplyRun) -> None:
        """Put the live link back on ``previous_target`` if it moved (else no-op)."""
        ...


def _remove_staging(overlay: Path, run_id: str) -> None:
    store = overlay / STORE_DIRNAME
    if not store.is_dir() or store.is_symlink():
        return
    for path in store.glob(f".staging-{run_id}*"):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)


async def _fail(store: RunStore, run: ApplyRun, message: str) -> ApplyRun:
    return await asyncio.to_thread(
        store.transition,
        run.id,
        RunState.FAILED,
        message=message,
        expect=run.state,
        failure=INTERRUPTED,
    )


async def _preparing(
    store: RunStore, executor: RecoveringExecutor, run: ApplyRun
) -> ApplyRun:
    overlay = getattr(executor, "overlay_root", None)
    if isinstance(overlay, Path):
        await asyncio.to_thread(_remove_staging, overlay, run.id)
    return await _fail(store, run, "the updater stopped while preparing")


async def _migrating(
    store: RunStore, executor: RecoveringExecutor, run: ApplyRun
) -> ApplyRun:
    overlay = getattr(executor, "overlay_root", None)
    if isinstance(overlay, Path):
        await asyncio.to_thread(_remove_staging, overlay, run.id)
    try:
        await asyncio.to_thread(executor.undo_swap, run)
    except (OSError, ValueError) as exc:
        logger.critical(
            "AUDIT | PLUGIN_UPDATE | rollback_failed run=%s plugin=%s: %s (%s)",
            run.id,
            run.plugin,
            _UNDO_REFUSED,
            type(exc).__name__,
        )
        return await asyncio.to_thread(
            store.transition,
            run.id,
            RunState.ROLLBACK_FAILED,
            message=f"the updater stopped while migrating; rollback: {_UNDO_REFUSED}",
            expect=RunState.MIGRATING,
            failure=INTERRUPTED,
        )
    return await _fail(
        store,
        run,
        "the updater stopped while migrating; nothing changed on the running API",
    )


def _imported_version() -> str:
    from baselith.core._version import __version__

    return str(__version__)


def close_finalizing(
    store: RunStore, run: ApplyRun, *, actor: str | None, reason: str
) -> ApplyRun:
    """End a ``finalizing`` run now, by what the API serves (blocking).

    ``succeeded`` when the newest boot report shows the run's target (the
    release was installed and serves; the updater may still run the old
    code, which its heartbeat reports as ``updater_mismatch``), otherwise
    ``failed`` with :data:`FINALIZE_TIMEOUT`: the target no longer serves and
    nothing was rolled back. ``reason`` opens the journal message.
    """
    serving = latest_framework_version(store)
    if run.to_version is not None and serving == run.to_version:
        return store.transition(
            run.id,
            RunState.SUCCEEDED,
            actor=actor,
            message=f"{reason}; {run.to_version} serves, the updater is not "
            "restarted onto it yet (updater_mismatch until it is)",
            expect=RunState.FINALIZING,
        )
    return store.transition(
        run.id,
        RunState.FAILED,
        actor=actor,
        message=f"{reason}; the API reports {serving or 'no version'}, not "
        f"{run.to_version}: nothing was rolled back",
        expect=RunState.FINALIZING,
        failure=FINALIZE_TIMEOUT,
    )


async def settle_finalizing(
    store: RunStore,
    run: ApplyRun,
    framework_version: str,
    *,
    now: datetime | None = None,
    timeout_seconds: int | None = None,
) -> ApplyRun | None:
    """Close a ``finalizing`` run when it is done, or when it waited too long.

    Done: this updater imported the version it installed and the newest boot
    report shows it (``succeeded``). Past ``timeout_seconds`` since it entered
    ``finalizing`` it is closed by :func:`close_finalizing`, so a run whose
    updater never restarts onto the target (no command, a failed command,
    another interpreter) cannot hold the core's claim forever. None: left
    alone for now.
    """
    serving = await asyncio.to_thread(latest_framework_version, store)
    if (
        run.to_version is not None
        and framework_version == run.to_version
        and serving == run.to_version
    ):
        return await asyncio.to_thread(
            store.transition,
            run.id,
            RunState.SUCCEEDED,
            message=f"the updater restarted on {run.to_version}",
            expect=RunState.FINALIZING,
        )
    if timeout_seconds is None:
        return None
    at = now or datetime.now(UTC)
    if at - run.updated_at < timedelta(seconds=timeout_seconds):
        return None
    logger.warning(
        "AUDIT | CORE_UPDATE | finalize_timeout run=%s to=%s serving=%s",
        run.id,
        run.to_version,
        serving,
    )
    reason = f"still finalizing after {timeout_seconds}s"
    return await asyncio.to_thread(
        close_finalizing, store, run, actor="timeout", reason=reason
    )


async def _handle(
    store: RunStore,
    executor: RecoveringExecutor,
    run: ApplyRun,
    framework_version: str,
    now: datetime | None,
    finalize_timeout: int | None,
) -> ApplyRun | None:
    if run.state is RunState.PREPARING:
        return await _preparing(store, executor, run)
    if run.state is RunState.MIGRATING:
        return await _migrating(store, executor, run)
    if run.state in (RunState.ACTIVATING, RunState.HEALTH_CHECKING):
        return await executor.resume_activation(run)
    if run.state is RunState.ROLLING_BACK:
        return await executor.redo_rollback(run)
    if run.state is RunState.FINALIZING:
        return await settle_finalizing(
            store, run, framework_version, now=now, timeout_seconds=finalize_timeout
        )
    return None  # approved, awaiting approval, rollback_failed: not ours to touch


async def reconcile(
    store: RunStore,
    executor: RecoveringExecutor,
    *,
    now: datetime | None = None,
    framework_version: str | None = None,
    finalize_timeout_seconds: int | None = None,
) -> list[str]:
    """Bring every interrupted run to a terminal state; the ids handled.

    Stale approval requests are expired first and stale expectations cleared
    last. When one run's recovery raises, the others are still settled and
    the expectations cleared before the first error is re-raised. A run that ends ``rollback_failed`` emits
    ``plugin.update_rollback_failed``. ``framework_version`` is the
    distribution version this updater imported (default: read it);
    ``finalize_timeout_seconds`` bounds a ``finalizing`` run (None: no bound).
    """
    imported = framework_version or _imported_version()
    await asyncio.to_thread(store.expire_stale, now)
    handled: list[str] = []
    first_error: Exception | None = None
    for run in await asyncio.to_thread(store.unfinished):
        try:
            done = await _handle(
                store, executor, run, imported, now, finalize_timeout_seconds
            )
        except RunStateConflict:  # it moved while we looked: someone else owns it
            continue
        except Exception as exc:  # settle the others first, then re-raise
            logger.error(
                "plugin_update_reconcile_failed run=%s plugin=%s error=%s",
                run.id,
                run.plugin,
                type(exc).__name__,
            )
            first_error = first_error or exc
            continue
        if done is None:
            continue
        handled.append(run.id)
        logger.warning(
            "AUDIT | PLUGIN_UPDATE | reconciled run=%s plugin=%s from=%s to=%s",
            run.id,
            run.plugin,
            run.state.value,
            done.state.value,
        )
        await announce_rollback_failed(done)
    await asyncio.to_thread(store.clear_stale_expectations)
    if first_error is not None:
        raise first_error
    return handled


__all__ = [
    "FINALIZE_TIMEOUT",
    "INTERRUPTED",
    "RecoveringExecutor",
    "close_finalizing",
    "reconcile",
    "settle_finalizing",
]
