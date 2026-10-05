"""The plugin updater service: one process per host, never serving HTTP.

``baselith plugin-updater serve`` runs :func:`serve`: it takes a
single-instance lock, publishes a heartbeat, reconciles what a previous
process left, then executes approved runs one at a time. It never imports the
``plugins`` package, never starts an error reporter (the schema owner's
credentials are a local of the executor) and runs every blocking store call
in a worker thread.

**Crash policy.** An executor call that raises unexpectedly is reconciled at
once (:func:`~.reconcile.reconcile`), so the run cannot stay stranded holding
its plugin's claim. If the run is still not finished after that — the
executor failed before it even left ``approved`` — :func:`serve` re-raises:
the process exits non-zero, systemd restarts it (``Restart=on-failure``) and
the next start reconciles again. A repeating failure trips the unit's start
limit and leaves it ``failed``, visibly, instead of looping.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from core.config.plugin_update_apply import UpdateApplyConfig

from ._events import announce_rollback_failed
from .executor import Executor
from .models import RELEASING_STATES, ApplyRun, RunState, UpdaterHeartbeat
from .reconcile import RecoveringExecutor, reconcile
from .store import RunStore

logger = logging.getLogger(__name__)

LOCK_FILENAME = "updater.lock"
#: How often the loop prunes old finished runs (``RunStore.prune_finished``).
PRUNE_INTERVAL_SECONDS = 3600.0


class UpdaterRefused(RuntimeError):
    """The updater cannot start here (the CLI exits 2; systemd does not retry)."""


class RunExecutor(RecoveringExecutor, Protocol):
    """What :func:`serve` drives: reconciliation entry points plus ``execute``."""

    async def execute(self, run: ApplyRun) -> ApplyRun: ...


def acquire_single_instance(path: Path) -> int:
    """Hold an exclusive, non-blocking ``flock`` on ``path``; the open fd.

    The fd is not inheritable (PEP 446), so no child process keeps the lock.

    Raises:
        UpdaterRefused: Another process holds it ("another plugin updater is running").
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise UpdaterRefused("another plugin updater is running") from None
    return fd


def _overlay_writable(root: Path | None) -> bool:
    return root is not None and root.is_dir() and os.access(root, os.W_OK | os.X_OK)


def _plugin_code_loaded() -> bool:
    return any(m == "plugins" or m.startswith("plugins.") for m in sys.modules)


async def _heartbeat(
    config: UpdateApplyConfig,
    store: RunStore,
    overlay_root: Path | None,
    core_version: str,
    stop: asyncio.Event,
) -> None:
    started = datetime.now(UTC)
    while not stop.is_set():
        try:
            writable = await asyncio.to_thread(_overlay_writable, overlay_root)
            beat = UpdaterHeartbeat(
                pid=os.getpid(),
                started_at=started,
                at=datetime.now(UTC),
                core_version=core_version,
                enabled=config.enabled,
                overlay_root=str(overlay_root) if overlay_root else None,
                overlay_writable=writable,
                restart_configured=config.restart_configured,
            )
            await asyncio.to_thread(store.write_heartbeat, beat)
        except Exception as exc:  # keep beating; the console flags a stale one
            logger.warning(
                "plugin_updater_heartbeat_failed error=%s", type(exc).__name__
            )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), config.heartbeat_seconds)


async def _run_one(store: RunStore, executor: RunExecutor, run: ApplyRun) -> None:
    """Execute ``run``; on an unexpected exception reconcile it before going on."""
    try:
        done = await executor.execute(run)
    except Exception as exc:
        # Type name only: no traceback, whose frames may hold credentials.
        logger.error(
            "plugin_updater_run_crashed run=%s plugin=%s error=%s",
            run.id,
            run.plugin,
            type(exc).__name__,
        )
        await reconcile(store, executor)
        after = await asyncio.to_thread(store.get, run.id)
        if after is not None and after.state not in RELEASING_STATES | {
            RunState.ROLLBACK_FAILED
        }:
            raise  # still stranded: exit non-zero, reconcile again at next start
        return
    await announce_rollback_failed(done)


async def _prune(store: RunStore) -> None:
    try:
        removed = await asyncio.to_thread(store.prune_finished)
    except Exception as exc:  # retention is housekeeping: never stop the loop
        logger.warning("plugin_updater_prune_failed error=%s", type(exc).__name__)
        return
    if removed:
        logger.info("plugin_updater_pruned runs=%d", len(removed))


async def serve(
    *,
    config: UpdateApplyConfig,
    store: RunStore,
    executor: RunExecutor,
    overlay_root: Path | None,
    core_version: str,
    stop: asyncio.Event,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Heartbeat, reconcile, then execute approved runs one at a time until ``stop``.

    Old finished runs are pruned when the loop starts and at most once every
    :data:`PRUNE_INTERVAL_SECONDS` after that.

    Raises:
        UpdaterRefused: Another updater runs, or plugin code was imported.
        Exception: An executor failure reconciliation could not settle.
    """
    fd = acquire_single_instance(store.root / LOCK_FILENAME)
    # Its own stop: a run in flight when ``stop`` is set keeps the heartbeat
    # fresh until it finishes, so the console never sees the updater offline.
    beating = asyncio.Event()
    heart: asyncio.Task[None] | None = None
    try:
        if _plugin_code_loaded():
            raise UpdaterRefused("plugin updater imported plugin code; refusing to run")
        heart = asyncio.create_task(
            _heartbeat(config, store, overlay_root, core_version, beating)
        )
        await reconcile(store, executor)
        pruned_at: float | None = None
        while not stop.is_set():
            now = clock()
            if pruned_at is None or now - pruned_at >= PRUNE_INTERVAL_SECONDS:
                pruned_at = now
                await _prune(store)
            await asyncio.to_thread(store.expire_stale)
            run = await asyncio.to_thread(store.next_approved)
            if run is not None:
                await _run_one(store, executor, run)
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), config.poll_seconds)
    finally:
        stop.set()
        beating.set()
        if heart is not None:
            await heart
        os.close(fd)


def build_executor(config: UpdateApplyConfig) -> tuple[Executor, Path]:
    """The production executor and the overlay root it installs into.

    Trusted keys are re-read for every run; the release sources file is read
    once (restart the updater after changing it).

    Raises:
        UpdaterRefused: ``BASELITH_PLUGIN_OVERLAY_DIR`` is unset or missing.
    """
    from core._core_version import CORE_VERSION
    from core.config.plugin_updates import get_plugin_update_config
    from core.config.plugins import get_plugin_config
    from core.plugins.overlay import OVERLAY_ENV, overlay_root
    from core.plugins.signing import load_trust_roots

    from ..archive import UNPACKED_SIZE_FACTOR
    from ..cache import UpdateCache
    from ..sources import GitHubReleaseSource, load_sources
    from ._io import GitHubReleaseFetcher, http_probe, subprocess_runner

    root = overlay_root()
    if root is None:
        raise UpdaterRefused(
            f"{OVERLAY_ENV} is unset or not a directory: the updater has nowhere to install"
        )
    update_cfg = get_plugin_update_config()
    max_bytes = update_cfg.max_artifact_mb * 1024 * 1024
    source = GitHubReleaseSource(
        update_cfg.github_api_url, update_cfg.github_token, max_bytes=max_bytes
    )
    sources_file = update_cfg.sources_file
    sources = (
        load_sources(sources_file)
        if sources_file is not None and sources_file.is_file()
        else {}
    )

    def trusted_keys(plugin: str) -> list[str]:
        return load_trust_roots(plugin)

    async def probe() -> int:
        return await http_probe(config.health_url)

    executor = Executor(
        store=RunStore(config.state_dir),
        config=config,
        overlay_root=root,
        bundled_root=Path(get_plugin_config().plugins_path),
        cache=UpdateCache(update_cfg.cache_dir),
        fetcher=GitHubReleaseFetcher(source, sources),
        runner=subprocess_runner,
        probe=probe,
        trusted_keys=trusted_keys,
        core_version=CORE_VERSION,
        max_unpacked_bytes=max_bytes * UNPACKED_SIZE_FACTOR,
    )
    return executor, root


__all__ = [
    "LOCK_FILENAME",
    "PRUNE_INTERVAL_SECONDS",
    "RunExecutor",
    "UpdaterRefused",
    "acquire_single_instance",
    "build_executor",
    "serve",
]
