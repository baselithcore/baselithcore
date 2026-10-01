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
``rollback_failed``       untouched: an operator resolves it
========================  ==================================================

Every interrupted run fails with the code ``interrupted``. Expectations of
finished runs are dropped last. All blocking work runs in worker threads.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Protocol

from core.plugins.overlay import STORE_DIRNAME

from ._events import announce_rollback_failed
from .models import ApplyRun, RunState
from .store import RunStateConflict, RunStore

logger = logging.getLogger(__name__)

INTERRUPTED = "interrupted"
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


async def _handle(
    store: RunStore, executor: RecoveringExecutor, run: ApplyRun
) -> ApplyRun | None:
    if run.state is RunState.PREPARING:
        return await _preparing(store, executor, run)
    if run.state is RunState.MIGRATING:
        return await _migrating(store, executor, run)
    if run.state in (RunState.ACTIVATING, RunState.HEALTH_CHECKING):
        return await executor.resume_activation(run)
    if run.state is RunState.ROLLING_BACK:
        return await executor.redo_rollback(run)
    return None  # approved, awaiting approval, rollback_failed: not ours to touch


async def reconcile(
    store: RunStore, executor: RecoveringExecutor, *, now: datetime | None = None
) -> list[str]:
    """Bring every interrupted run to a terminal state; the ids handled.

    Stale approval requests are expired first and stale expectations cleared
    last. When one run's recovery raises, the others are still settled and
    the expectations cleared before the first error is re-raised. A run that ends ``rollback_failed`` emits
    ``plugin.update_rollback_failed``.
    """
    await asyncio.to_thread(store.expire_stale, now)
    handled: list[str] = []
    first_error: Exception | None = None
    for run in await asyncio.to_thread(store.unfinished):
        try:
            done = await _handle(store, executor, run)
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


__all__ = ["INTERRUPTED", "RecoveringExecutor", "reconcile"]
