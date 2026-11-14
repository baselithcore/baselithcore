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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from baselith.core.config.plugin_update_apply import UpdateApplyConfig

from ._events import announce_rollback_failed
from ._io import CommandRunner, subprocess_runner
from ._policy_gate import admit_policy_run
from .drivers import (
    CORE_HOST_DRIVER,
    PLUGIN_OVERLAY_DRIVER,
    DriverRegistry,
    DriverRouter,
)
from .executor import Executor
from .models import RELEASING_STATES, ApplyRun, RunState, UpdaterHeartbeat
from .reconcile import RecoveringExecutor, reconcile, settle_finalizing
from .store import RunStore

if TYPE_CHECKING:
    from ..sources import GitHubReleaseSource
    from .policy_runner import PolicyRunner

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


def _framework_version() -> str:
    """The ``__version__`` this process imported (a later checkout does not change it)."""
    from baselith.core._version import __version__

    return str(__version__)


def _plugin_code_loaded() -> bool:
    return any(m == "plugins" or m.startswith("baselith.plugins.") for m in sys.modules)


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
                framework_version=_framework_version(),
            )
            await asyncio.to_thread(store.write_heartbeat, beat)
        except Exception as exc:  # keep beating; the console flags a stale one
            logger.warning(
                "plugin_updater_heartbeat_failed error=%s", type(exc).__name__
            )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), config.heartbeat_seconds)


async def _run_one(
    store: RunStore, executor: RunExecutor, run: ApplyRun
) -> ApplyRun | None:
    """Execute ``run``; on an unexpected exception reconcile it before going on.

    Returns the run as ``execute`` left it (None after a reconciled crash).
    """
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
        return None
    await announce_rollback_failed(done)
    return done


async def _restart_self(
    config: UpdateApplyConfig, run: ApplyRun, runner: CommandRunner
) -> bool:
    """Run ``UPDATE_APPLY_UPDATER_RESTART_COMMAND`` for a ``finalizing`` run.

    The run is already ``finalizing`` in the store, so whatever happens to
    this process, reconciliation at the next start closes it. True when the
    command succeeded (the caller then stops: the command replaces this
    process); False without a command or when it failed, and the updater
    keeps running the old code (``updater_mismatch`` explains it). The
    command must not wait for the restart (``systemctl --no-block restart``):
    a blocking restart of this process's own unit waits for this process to
    stop, which waits for the command, until ``TimeoutStopSec`` kills it.
    """
    if not config.updater_restart_command:
        logger.warning(
            "core_update_finalizing run=%s: no UPDATE_APPLY_UPDATER_RESTART_COMMAND; "
            "restart the updater to finish",
            run.id,
        )
        return False
    logger.info("AUDIT | CORE_UPDATE | updater restarting itself run=%s", run.id)
    rc = await asyncio.to_thread(
        runner,
        list(config.updater_restart_command),
        timeout=float(config.restart_timeout_seconds),
    )
    if rc != 0:
        logger.error("core_update_updater_restart_failed run=%s rc=%s", run.id, rc)
        return False
    return True


async def _settle_finalizing(
    store: RunStore, config: UpdateApplyConfig, at: datetime
) -> None:
    """Close every ``finalizing`` run past ``UPDATE_APPLY_FINALIZE_TIMEOUT_SECONDS``."""
    from baselith.core._version import __version__

    try:
        waiting = await asyncio.to_thread(store.runs, {RunState.FINALIZING})
        for run in waiting:
            await settle_finalizing(
                store,
                run,
                str(__version__),
                now=at,
                timeout_seconds=config.finalize_timeout_seconds,
            )
    except Exception as exc:  # housekeeping: never stop the loop
        logger.warning("core_update_settle_failed error=%s", type(exc).__name__)


async def _prune(store: RunStore) -> None:
    try:
        removed = await asyncio.to_thread(store.prune_finished)
    except Exception as exc:  # retention is housekeeping: never stop the loop
        logger.warning("plugin_updater_prune_failed error=%s", type(exc).__name__)
        return
    if removed:
        logger.info("plugin_updater_pruned runs=%d", len(removed))


async def _policy_tick(policy_runner: PolicyRunner) -> None:
    try:
        created = await policy_runner.tick()
    except Exception as exc:  # the policy never stops the updater
        logger.warning("update_policy_tick_failed error=%s", type(exc).__name__)
        return
    if created:
        logger.info("update_policy_created runs=%s", ",".join(created))


async def serve(
    *,
    config: UpdateApplyConfig,
    store: RunStore,
    executor: RunExecutor,
    overlay_root: Path | None,
    core_version: str,
    stop: asyncio.Event,
    clock: Callable[[], float] = time.monotonic,
    runner: CommandRunner = subprocess_runner,
    policy_runner: PolicyRunner | None = None,
    wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Heartbeat, reconcile, then execute approved runs one at a time until ``stop``.

    Old finished runs are pruned when the loop starts and at most once every
    :data:`PRUNE_INTERVAL_SECONDS` after that. With a ``policy_runner`` its
    tick runs first and then every ``UPDATE_APPLY_POLICY_TICK_SECONDS``,
    before the next approved run is picked; a tick that raises is logged and
    the loop goes on. A run in flight is never interrupted by the window
    closing: ticks only run between runs. A policy run is admitted only while
    its window is still open and its estimate fits
    (:func:`._policy_gate.admit_policy_run`), otherwise it is deferred to the
    next window instead of starting. A run that ends ``finalizing``
    (a core install) makes the updater restart itself last, through
    ``UPDATE_APPLY_UPDATER_RESTART_COMMAND`` run by ``runner``, and stop; a
    ``finalizing`` run still open after ``UPDATE_APPLY_FINALIZE_TIMEOUT_SECONDS``
    (by ``wall_clock``) is closed by what the API serves, so it never holds the
    core's claim for good.

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
        await reconcile(
            store,
            executor,
            now=wall_clock(),
            finalize_timeout_seconds=config.finalize_timeout_seconds,
        )
        pruned_at: float | None = None
        ticked_at: float | None = None
        while not stop.is_set():
            now = clock()
            if pruned_at is None or now - pruned_at >= PRUNE_INTERVAL_SECONDS:
                pruned_at = now
                await _prune(store)
            await asyncio.to_thread(store.expire_stale)
            await _settle_finalizing(store, config, wall_clock())
            if policy_runner is not None and (
                ticked_at is None or now - ticked_at >= config.policy_tick_seconds
            ):
                ticked_at = now
                await _policy_tick(policy_runner)
            run = await asyncio.to_thread(store.next_approved)
            if run is not None:
                if not await admit_policy_run(store, config, run, now=wall_clock()):
                    continue  # deferred to the next window: pick the next run
                done = await _run_one(store, executor, run)
                if (
                    done is not None
                    and done.state is RunState.FINALIZING
                    and await _restart_self(config, done, runner)
                ):
                    break  # the command restarts this process; reconcile closes the run
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), config.poll_seconds)
    finally:
        stop.set()
        beating.set()
        if heart is not None:
            await heart
        os.close(fd)


def _register_core_driver(
    registry: DriverRegistry,
    config: UpdateApplyConfig,
    store: RunStore,
    source: GitHubReleaseSource,
    probe: Callable[[], Awaitable[int]],
) -> None:
    """Register ``core-host`` (built on first use) for this host's checkout."""
    from baselith.core import _version
    from baselith.core._core_version import CORE_VERSION
    from baselith.core.config.plugin_updates import get_plugin_update_config

    from .._core_state import distribution_keys
    from ..distribution import distribution_name
    from ..distribution_manifest import PUBLIC_CORE_NAME
    from ..upgrade import detect_install_method
    from .drivers import CoreHostDriver
    from .drivers._fetch import GitHubDistributionFetcher
    from .drivers._git import checkout_root, subprocess_git

    update_cfg = get_plugin_update_config()
    name = distribution_name()
    slug = (
        update_cfg.distribution_update_repo if name else update_cfg.core_update_repo
    ).strip()
    method, _ = detect_install_method(update_cfg)

    def build() -> CoreHostDriver:
        return CoreHostDriver(
            store=store,
            config=config,
            git=subprocess_git,
            runner=subprocess_runner,
            fetcher=GitHubDistributionFetcher(source, slug),
            probe=probe,
            trusted_keys=distribution_keys,
            distribution_name=name or PUBLIC_CORE_NAME,
            repo_root=checkout_root(),
            core_version=CORE_VERSION,
            framework_version=str(_version.__version__),
            install_method=method,
        )

    registry.register(CORE_HOST_DRIVER, build)


def build_executor(config: UpdateApplyConfig) -> tuple[RunExecutor, Path | None]:
    """The production executor and the overlay root it installs into.

    The plugin overlay :class:`Executor` is registered as the
    ``plugin-overlay`` driver and, when ``UPDATE_APPLY_CORE_ENABLED`` is on,
    :class:`~.drivers.CoreHostDriver` as ``core-host``; the result is a
    :class:`~.drivers.DriverRouter`, which hands each run to the driver its
    subject and driver name select. Trusted keys are re-read for every run;
    the release sources file is read once (restart the updater after changing
    it). With the core switch on, a host without an overlay runs core
    installs only (the root returned is then None).

    Raises:
        UpdaterRefused: ``BASELITH_PLUGIN_OVERLAY_DIR`` is unset or missing
            and the core switch is off: there is nothing to install.
    """
    from baselith.core._core_version import CORE_VERSION
    from baselith.core.config.plugin_updates import get_plugin_update_config
    from baselith.core.config.plugins import get_plugin_config
    from baselith.core.plugins.overlay import OVERLAY_ENV, overlay_root
    from baselith.core.plugins.signing import load_trust_roots

    from ..archive import UNPACKED_SIZE_FACTOR
    from ..cache import UpdateCache
    from ..sources import GitHubReleaseSource, load_sources
    from ._io import GitHubReleaseFetcher, http_probe

    root = overlay_root()
    if root is None and not config.core_enabled:
        raise UpdaterRefused(
            f"{OVERLAY_ENV} is unset or not a directory: the updater has nowhere to install"
        )
    update_cfg = get_plugin_update_config()
    max_bytes = update_cfg.max_artifact_mb * 1024 * 1024
    source = GitHubReleaseSource(
        update_cfg.github_api_url, update_cfg.github_token, max_bytes=max_bytes
    )

    async def probe() -> int:
        return await http_probe(config.health_url)

    store = RunStore(config.state_dir)
    registry = DriverRegistry()  # exactly the drivers built here, nothing stale
    if config.core_enabled:
        _register_core_driver(registry, config, store, source, probe)
    if root is None:
        return DriverRouter(store, resolve=registry.get), None
    sources_file = update_cfg.sources_file
    sources = (
        load_sources(sources_file)
        if sources_file is not None and sources_file.is_file()
        else {}
    )

    def trusted_keys(plugin: str) -> list[str]:
        return load_trust_roots(plugin)

    executor = Executor(
        store=store,
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
    registry.register(PLUGIN_OVERLAY_DRIVER, lambda: executor)
    return DriverRouter(store, resolve=registry.get), root


__all__ = [
    "LOCK_FILENAME",
    "PRUNE_INTERVAL_SECONDS",
    "RunExecutor",
    "UpdaterRefused",
    "acquire_single_instance",
    "build_executor",
    "serve",
]
