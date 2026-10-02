"""Execute one install or rollback run; called only by the updater.

An update fetches the signed ``release.json`` and holds it to the tarball
SHA-256 pinned on the run when it was requested (never re-derived from what is
downloaded now), stages the release under the per-plugin run lock, runs
``schema-init`` with the owner's credentials against a *scratch* overlay where
the plugin already resolves to the new entry, and only then — immediately
before the restart — switches the live overlay link, restarts the API and
waits for a healthy verdict (or switches back and restarts again).

The live link moves last on purpose: the running API resolves
``plugins.<name>`` through it for lazy imports, on-demand activation and
respawned workers, so while ``schema-init`` runs it must still point at the
code the API booted with. A failed ``schema-init`` therefore changes nothing
on the running API. This module must never import the ``plugins`` package.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.config.plugin_update_apply import UpdateApplyConfig
from core.plugins._overlay_guard import OverlayRefusal, overlay_refusal_code
from core.plugins.overlay import OVERLAY_ENV, STORE_DIRNAME, verify_overlay_entry

from ..cache import UpdateCache
from ..release_manifest import ReleaseManifestError, load_release_json
from ..signed_assets import _host_build_required, manifest_refusal
from ..sources import safe_error
from ._activation import NO_BOOT_REPORT, ActivationMixin
from ._io import CommandRunner, ReleaseFetcher, SchemaEnvError, schema_env
from ._scratch import make_scratch_overlay
from .models import ApplyRun, RunKind, RunState
from .staging import StagingError, known_files_of, stage_release
from .store import RunStateConflict, RunStore
from .swap import current_target, point_to

logger = logging.getLogger(__name__)

_SCHEMA_INIT_TIMEOUT = 600.0
_BAD_LINK = "the plugin's overlay path is not a store link"
UNTOUCHED = "nothing changed on the running API"
_HOST_BUILD = "the release needs a host build (host_build_required); install it by hand"


def SCHEMA_INIT_ARGV(plugin: str) -> list[str]:
    """argv of ``baselith plugin schema-init --plugin <plugin>`` in this interpreter."""
    return [
        sys.executable,
        "-m",
        "core.cli",
        "plugin",
        "schema-init",
        "--plugin",
        plugin,
    ]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _needs_host_build(tarball: Path, plugin: str, pinned_sha256: str) -> bool:
    """The pinned tarball's manifest sets ``host_build_required``.

    Only a tarball matching the pin is read; any other is left to staging,
    which refuses it as ``artifact_checksum``.
    """
    digest = hashlib.sha256()
    with tarball.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != pinned_sha256.lower():
        return False
    return _host_build_required(tarball, plugin)


class Executor(ActivationMixin):
    """Runs a run's state machine; every transition is journaled first."""

    def __init__(
        self,
        *,
        store: RunStore,
        config: UpdateApplyConfig,
        overlay_root: Path,
        bundled_root: Path,
        cache: UpdateCache,
        fetcher: ReleaseFetcher,
        runner: CommandRunner,
        probe: Callable[[], Awaitable[int]],
        trusted_keys: Callable[[str], list[str]],
        core_version: str,
        max_unpacked_bytes: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """Wire the executor; ``now`` must be the wall clock workers stamp boot reports with."""
        self._store, self._config = store, config
        self._overlay, self._bundled = overlay_root, bundled_root
        self._cache, self._fetcher, self._runner = cache, fetcher, runner
        self._probe, self._keys, self._core = probe, trusted_keys, core_version
        self._max = max_unpacked_bytes
        self._clock, self._sleep = clock, sleep
        self._now = now or _utcnow

    @property
    def overlay_root(self) -> Path:
        """The overlay directory this executor installs into."""
        return self._overlay

    # -- entry points ---------------------------------------------------------

    async def execute(self, run: ApplyRun) -> ApplyRun:
        """Take an APPROVED run to a terminal state (or ROLLBACK_FAILED)."""
        try:
            run = await self._step(run, RunState.PREPARING, expect=RunState.APPROVED)
        except RunStateConflict:  # someone else moved it: leave it alone
            return await asyncio.to_thread(self._store.get, run.id) or run
        if not self._config.enabled:
            return await self._fail(run, "apply_disabled", "the kill switch is off")
        if not self._config.restart_configured:
            return await self._fail(
                run, "apply_disabled", "no restart command configured"
            )
        survivors = await self._survivors(run.plugin)
        if survivors is None:  # fail closed: nothing known about what must survive
            logger.warning(
                "plugin_update_no_boot_report run=%s plugin=%s", run.id, run.plugin
            )
            return await self._fail(run, "apply_disabled", NO_BOOT_REPORT)
        if run.kind is RunKind.ROLLBACK:
            return await self._rollback_run(run, survivors)
        env: dict[str, str] | None = None
        if self._config.schema_init:
            try:  # refused before staging: never fall back to runtime creds
                env = await asyncio.to_thread(schema_env, self._config)
            except SchemaEnvError as exc:
                return await self._fail(run, "migration_failed", str(exc))
        try:
            staged, previous = await self._prepare(run)
        except StagingError as exc:
            return await self._fail(run, exc.code, exc.detail)
        run = await self._step(
            run,
            RunState.MIGRATING,
            previous_target=previous,
            target=staged,
            must_stay_active=survivors,
        )
        if env is not None:
            rc = await self._schema_init(run, staged, env)
            if rc != 0:
                detail = f"schema-init exited {rc}; {UNTOUCHED}"
                return await self._fail(run, "migration_failed", detail)
        return await self._activate(run, run.to_version, staged)

    async def resume_activation(self, run: ApplyRun) -> ApplyRun:
        """Reconcile: the updater died while activating; restart and check again."""
        version = await asyncio.to_thread(self._version_of, run.plugin, run.target)
        return await self._activate(run, version, run.target)

    async def redo_rollback(self, run: ApplyRun) -> ApplyRun:
        """Reconcile: the updater died while rolling back; do it again (idempotent)."""
        detail = run.message.partition("; rollback: ")[0]
        if detail.startswith("rollback: "):
            detail = ""
        return await self._roll_back(run, run.failure or "interrupted", detail)

    def undo_swap(self, run: ApplyRun) -> None:
        """Reconcile: put the link back on ``previous_target`` if it moved.

        A run switches the live link only right before its restart, so an
        interrupted ``migrating`` run normally left it alone: then this is a
        no-op. It still restores a link an older updater had switched early.
        """
        with self._store.plugin_lock(run.plugin):
            if current_target(self._overlay, run.plugin) != run.previous_target:
                point_to(self._overlay, run.plugin, run.previous_target, run_id=run.id)

    # -- an update ------------------------------------------------------------

    def _schema_scratch_locked(self, run: ApplyRun, target: str) -> Path:
        with self._store.plugin_lock(run.plugin):
            return make_scratch_overlay(
                self._overlay,
                run_id=run.id,
                label="schema",
                plugin=run.plugin,
                target=target,
                with_others=True,
            )

    async def _schema_init(
        self, run: ApplyRun, target: str, env: dict[str, str]
    ) -> int:
        """Run ``schema-init`` with the plugin resolving to ``target`` — live link untouched.

        The subprocess gets ``BASELITH_PLUGIN_OVERLAY_DIR`` pointed at a
        scratch overlay (the other overlaid plugins linked as they are live),
        removed on every path. Returns the exit code (-1: it could not start).
        """
        try:
            scratch = await asyncio.to_thread(self._schema_scratch_locked, run, target)
        except OSError as exc:
            logger.error(
                "plugin_update_schema_scratch_failed run=%s error=%s",
                run.id,
                type(exc).__name__,
            )
            return -1
        try:
            return await asyncio.to_thread(
                self._runner,
                SCHEMA_INIT_ARGV(run.plugin),
                env={**env, OVERLAY_ENV: str(scratch)},
                timeout=_SCHEMA_INIT_TIMEOUT,
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    async def _release_meta(self, run: ApplyRun, keys: list[str]) -> dict[str, Any]:
        assert run.to_version is not None and run.tarball_sha256 is not None
        try:
            raw = await self._fetcher.release_json(run.plugin, run.to_version)
            meta = load_release_json(raw)
        except ReleaseManifestError as exc:
            raise StagingError("verification_failed", str(exc)) from exc
        except Exception as exc:  # untrusted network: never crash the run
            raise StagingError("verification_failed", safe_error(exc)) from exc
        refused = manifest_refusal(meta, keys)
        if refused is not None:
            raise StagingError(
                "verification_failed", f"{refused[0].value}: {refused[1]}"
            )
        if (
            meta.get("name") != run.plugin
            or str(meta.get("version")) != run.to_version
            or str(meta.get("tarball_sha256", "")).lower() != run.tarball_sha256.lower()
        ):
            raise StagingError(
                "release_changed", "the release changed since it was requested"
            )
        return meta

    async def _ensure_tarball(self, run: ApplyRun) -> Path:
        assert run.to_version is not None
        tarball = self._cache.tarball_path(run.plugin, run.to_version)
        if await asyncio.to_thread(tarball.is_file):
            return tarball  # whatever it holds, staging checks it against the pin
        await asyncio.to_thread(tarball.parent.mkdir, parents=True, exist_ok=True)
        fd, name = await asyncio.to_thread(
            tempfile.mkstemp, dir=tarball.parent, prefix=".download-"
        )
        tmp = Path(name)
        try:
            await asyncio.to_thread(os.close, fd)
            await self._fetcher.tarball(run.plugin, run.to_version, tmp)
            await asyncio.to_thread(tmp.replace, tarball)
        except Exception as exc:
            raise StagingError("verification_failed", safe_error(exc)) from exc
        finally:
            await asyncio.to_thread(tmp.unlink, missing_ok=True)
        return tarball

    async def _prepare(self, run: ApplyRun) -> tuple[str, str | None]:
        """Stage the pinned release; ``(store entry, link target before the run)``."""
        if not run.to_version or not run.tarball_sha256:
            raise StagingError("verification_failed", "the run pins no release")
        keys = await asyncio.to_thread(self._keys, run.plugin)
        meta = await self._release_meta(run, keys)
        tarball = await self._ensure_tarball(run)
        try:
            host_build = await asyncio.to_thread(
                _needs_host_build, tarball, run.plugin, run.tarball_sha256
            )
        except Exception as exc:  # unreadable archive: staging says precisely why
            logger.warning(
                "plugin_update_host_build_unread run=%s error=%s",
                run.id,
                type(exc).__name__,
            )
            host_build = False
        if host_build:
            raise StagingError("verification_failed", _HOST_BUILD)
        return await asyncio.to_thread(self._stage_locked, run, meta, tarball, keys)

    def _stage_locked(
        self, run: ApplyRun, meta: dict[str, Any], tarball: Path, keys: list[str]
    ) -> tuple[str, str | None]:
        assert run.to_version is not None and run.tarball_sha256 is not None
        with self._store.plugin_lock(run.plugin):
            try:
                previous = current_target(self._overlay, run.plugin)
            except ValueError as exc:
                raise StagingError("overlay_refused", _BAD_LINK) from exc
            current = (
                self._overlay / STORE_DIRNAME / previous
                if previous
                else self._bundled / run.plugin
            )
            current_dir = current if current.is_dir() else None
            known = (
                known_files_of(current_dir, overlay_root=self._overlay)
                if current_dir
                else set()
            )
            staged = stage_release(
                plugin=run.plugin,
                version=run.to_version,
                tarball=tarball,
                meta=meta,
                pinned_sha256=run.tarball_sha256,  # the approval-time pin, unchanged
                overlay_root=self._overlay,
                current_dir=current_dir,
                known_files=known,
                installed_version=run.from_version,
                bundled_root=self._bundled,
                core_version=self._core,
                trusted_keys=keys,
                run_id=run.id,
                max_unpacked_bytes=self._max,
            )
        return staged.name, previous

    # -- a rollback run -------------------------------------------------------

    def _verify_target_locked(
        self, run: ApplyRun, target: str, keys: list[str]
    ) -> tuple[str, str] | None:
        """Verify ``.store/<target>`` as the loader would, without linking it.

        A scratch ``.store/.staging-<run>-verify-*/`` holds ``.store -> ..``
        and ``<plugin> -> .store/<target>``, so the entry is judged under the
        plugin's own name (signature, name, core bounds, bundled version)
        while the live link is untouched. Returns ``(code, detail)`` or None.
        """
        plugin = run.plugin
        store = self._overlay / STORE_DIRNAME
        entry = store / target
        if (
            "/" in target
            or os.sep in target
            or not target.startswith(f"{plugin}-")
            or entry.is_symlink()
            or not entry.is_dir()
        ):
            return "missing", ""
        with self._store.plugin_lock(plugin):
            scratch = make_scratch_overlay(
                self._overlay,
                run_id=run.id,
                label="verify",
                plugin=plugin,
                target=target,
            )
            try:
                link = scratch / plugin
                reason = verify_overlay_entry(link, keys)
                if reason is not None:
                    return reason, ""
                refused = overlay_refusal_code(link, self._bundled, self._core)
                return None if refused is None else (refused[0].value, refused[1])
            finally:
                shutil.rmtree(scratch, ignore_errors=True)

    async def _rollback_run(self, run: ApplyRun, survivors: list[str]) -> ApplyRun:
        """Switch to ``run.target`` (a verified store entry, or None = bundled)."""
        target = run.target
        try:
            previous = await asyncio.to_thread(
                current_target, self._overlay, run.plugin
            )
        except ValueError:
            return await self._fail(run, "overlay_refused", _BAD_LINK)
        if target is not None:
            keys = await asyncio.to_thread(self._keys, run.plugin)
            try:
                refused = await asyncio.to_thread(
                    self._verify_target_locked, run, target, keys
                )
            except OSError as exc:  # scratch dir, symlink or hash walk: no path shown
                refused = ("unreadable", type(exc).__name__)
            if refused is not None and refused[0] == OverlayRefusal.NOT_NEWER:
                target = None  # the bundled plugin has overtaken it: go to bundled
            elif refused is not None:
                code, detail = refused
                shown = f"{code}: {detail}" if detail else code
                return await self._fail(
                    run, "verification_failed", f"rollback target {target}: {shown}"
                )
        run = await self._step(
            run,
            RunState.MIGRATING,
            previous_target=previous,
            target=target,
            must_stay_active=survivors,
        )
        version = await asyncio.to_thread(self._version_of, run.plugin, target)
        return await self._activate(run, version, target)


__all__ = ["NO_BOOT_REPORT", "SCHEMA_INIT_ARGV", "Executor"]
