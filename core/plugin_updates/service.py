"""Plugin update service: cached report, on-demand check and periodic loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from core import _version
from core._core_version import CORE_VERSION
from core._version import __version__ as FRAMEWORK_VERSION
from core.config.plugin_update_apply import UpdateApplyConfig, get_update_apply_config
from core.config.plugin_updates import PluginUpdateConfig
from core.config.plugins import get_plugin_config
from core.events import get_event_bus
from core.plugins.signing import load_trust_roots

from .announce import AnnouncementGate, build_announcement_gate
from .apply.eligibility import already_reached, apply_status
from .apply.models import UpdaterHeartbeat
from .apply.store import RunStore
from .cache import UpdateCache
from .checker import installed_versions, run_check
from .metrics import publish_update_metrics
from .models import CheckReport, SystemUpdate, UpdateCandidate
from .sources import GitHubReleaseSource, load_sources, safe_error
from .system import carry_over, check_system
from .upgrade import (
    Deployment,
    build_upgrade_instructions,
    detect_install_method,
    installed_bounds,
    plugin_install_guidance,
    read_namespace,
)
from .upgrade_models import PluginBoundsIssue

logger = logging.getLogger(__name__)

FIRST_RUN_DELAY_SECONDS = 60
#: A check requested over the API within this many seconds of the last one
#: completed in this process returns that report instead of calling GitHub.
CHECK_COOLDOWN_SECONDS = 60.0
EVENT_NAME = "plugin.update_available"
SYSTEM_EVENT_NAME = "system.update_available"


_safe_error = safe_error


def _apply_config_or_defaults() -> UpdateApplyConfig:
    """``UPDATE_APPLY_*`` settings; unloadable ones mean defaults (kill switch off).

    A bad one-click setting must never take the update checker down with it;
    only the error's type is logged (its text may echo a configured value).
    """
    try:
        return get_update_apply_config()
    except Exception as exc:
        logger.warning(
            "plugin_update_apply_config_invalid error=%s; one-click updates off",
            type(exc).__name__,
        )
        return UpdateApplyConfig.model_construct()


class PluginUpdateService:
    """Owns the update cache and the background check loop."""

    def __init__(
        self,
        config: PluginUpdateConfig,
        bundled_root: Path | None = None,
        gate: AnnouncementGate | None = None,
        *,
        apply_config: UpdateApplyConfig | None = None,
        run_store: RunStore | None = None,
    ) -> None:
        """Create the service; nothing runs until :meth:`start`."""
        self._config = config
        # The root the plugin loader scans (PLUGIN_PLUGINS_PATH, resolved),
        # not a cwd-relative ``plugins`` that misses an installed package.
        self._bundled_root = (
            bundled_root
            if bundled_root is not None
            else Path(get_plugin_config().plugins_path)
        )
        self._cache = UpdateCache(config.cache_dir)
        self._gate = (
            gate
            if gate is not None
            else build_announcement_gate(config.cache_dir, config.instance_id)
        )
        self._interval: float = config.check_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._last: tuple[float, CheckReport] | None = None
        self._deployment: Deployment | None = None
        self._bounds: list[PluginBoundsIssue] | None = None
        self._warned: set[str] = set()
        self._apply_config = apply_config or _apply_config_or_defaults()
        self._runs = run_store or RunStore(self._apply_config.state_dir)
        self._installed: dict[str, str] | None = None

    def report(self) -> CheckReport | None:
        """The last saved report, if any, as :meth:`_present` shapes it."""
        return self._present(self._cache.load())

    def deployment(self) -> Deployment:
        """This deployment's installation method and context (read once)."""
        if self._deployment is None:
            method, detected = detect_install_method(self._config)
            self._deployment = Deployment(
                method=method,
                detected=detected,
                framework_version=FRAMEWORK_VERSION,
                distribution=getattr(_version, "__distribution__", None),
                namespace=read_namespace() if method == "helm" else None,
                base_url=os.getenv("APP_BASE_URL", "").strip() or None,
            )
        return self._deployment

    def _warn_once(self, what: str, exc: Exception) -> None:
        """Log a presentation failure once per process, not on every read."""
        if what not in self._warned:
            self._warned.add(what)
            logger.warning("%s: %s", what, type(exc).__name__)

    def _plugin_bounds(self) -> list[PluginBoundsIssue] | None:
        """The installed plugins' core bounds, or None when they cannot be read.

        Plugins change only across a restart, so a successful scan is kept; a
        failed one is retried on the next read.
        """
        if self._bounds is None:
            try:
                self._bounds = installed_bounds(self._bundled_root)
            except Exception as exc:
                self._warn_once("plugin_bounds_scan_failed", exc)
                return None
        return self._bounds

    def _with_instructions(self, system: SystemUpdate) -> SystemUpdate:
        """``system`` with its upgrade instructions; without them on a failure."""
        try:
            upgrade = build_upgrade_instructions(
                system, self._config, self.deployment(), self._plugin_bounds()
            )
        except Exception as exc:
            self._warn_once("upgrade_instructions_failed", exc)
            upgrade = None
        return system.model_copy(update={"upgrade": upgrade})

    def _with_guidance(self, report: CheckReport) -> list[UpdateCandidate]:
        """The candidates, each available one with its install guidance."""
        try:
            guidance = plugin_install_guidance(self._config, self.deployment().method)
        except Exception as exc:
            self._warn_once("plugin_install_guidance_failed", exc)
            guidance = None
        try:
            heartbeat = self._runs.read_heartbeat()  # one snapshot per read
        except Exception as exc:
            self._warn_once("apply_heartbeat_failed", exc)
            heartbeat = None
        return [
            self._with_apply(
                c.model_copy(update={"install": guidance if c.available else None}),
                heartbeat,
            )
            for c in self._current_trust(report.candidates)
        ]

    def _with_apply(
        self, c: UpdateCandidate, heartbeat: UpdaterHeartbeat | None
    ) -> UpdateCandidate:
        """Hide what the running version already reached; stamp the verdict."""
        if not c.available:
            return c
        try:
            if self._installed is None:  # plugins change only across a restart
                self._installed = installed_versions(self._bundled_root)
            if already_reached(c, self._installed.get(c.plugin)):
                return c.model_copy(update={"available": False, "install": None})
            status = apply_status(
                c,
                config=self._apply_config,
                method=self.deployment().method,
                heartbeat=heartbeat,
                active_run=self._runs.active(c.plugin),
                core_version=CORE_VERSION,
                now=datetime.now(UTC),
            )
        except Exception as exc:
            self._warn_once("apply_status_failed", exc)
            return c
        return c.model_copy(update={"apply": status})

    def _current_trust(
        self, candidates: list[UpdateCandidate]
    ) -> list[UpdateCandidate]:
        """Only verdicts reached under today's ``PLUGIN_UPDATE_TRUST`` mode.

        A verdict saved (or carried over a failed check) under the other mode
        answers a question this deployment no longer asks: switching from
        signed to provenance must not keep offering a signed-only verdict, nor
        the reverse.
        """
        mode = self._config.trust
        return [c for c in candidates if c.trust == mode]

    def _present(self, report: CheckReport | None) -> CheckReport | None:
        """``report`` as served: current notice, instructions and guidance.

        The system notice goes through :meth:`_scope_system` and gains the
        version-specific upgrade instructions; every available plugin update
        gains its install guidance. Both are derived from the current
        configuration on each read and never cached. A plugin verdict reached
        under another trust mode is dropped. Neither can fail the
        read: a failure is logged once and the report is served without that
        part (a plugin scan failure reports the plugin check as not computed).
        """
        if report is None:
            return None
        system = self._scope_system(report.system)
        if system is not None:
            system = self._with_instructions(system)
        candidates = self._with_guidance(report)
        return report.model_copy(update={"system": system, "candidates": candidates})

    def _scope_system(self, system: SystemUpdate | None) -> SystemUpdate | None:
        """Serve only a notice about this core, with today's guide link.

        A saved notice about another repository or another installed version
        (a cache from before an upgrade, or from before the notice referenced
        the public core release) is dropped rather than shown, and so is one
        saved while the check was on and read after it was switched off. The
        upgrade guide link always comes from the current configuration.
        """
        if system is None:
            return None
        if (
            not self._config.system_checks_enabled
            or system.repo != self._config.core_update_repo.strip()
            or system.installed_version != CORE_VERSION
        ):
            return None
        guide = self._config.upgrade_guide_url
        if system.upgrade_guide_url == guide and system.upgrade is None:
            return system
        return system.model_copy(update={"upgrade_guide_url": guide, "upgrade": None})

    async def request_check(self) -> CheckReport:
        """``check_now`` for API callers, throttled per process.

        When a check completed less than :data:`CHECK_COOLDOWN_SECONDS` ago in
        this process, its report is returned and GitHub is not contacted, so a
        repeatedly pressed "Check now" cannot burn the API rate limit. The
        periodic loop calls :meth:`check_now` directly and is not throttled.
        """
        last = self._last
        if last is not None and time.monotonic() - last[0] < CHECK_COOLDOWN_SECONDS:
            return self._present(last[1]) or last[1]  # re-stamp: runs move fast
        return await self.check_now()

    async def check_now(self) -> CheckReport:
        """Run the plugin and system checks, save and announce new updates.

        The two parts are independent: either may be disabled, and a failure in
        one never hides the other's results. On a plugin failure the previous
        candidates are kept and ``error`` set.
        """
        async with self._lock:
            previous = self._cache.load()
            if previous is not None:
                previous = previous.model_copy(
                    update={"system": self._scope_system(previous.system)}
                )
            source = GitHubReleaseSource(
                self._config.github_api_url,
                self._config.github_token,
                max_bytes=self._config.max_artifact_mb * 1024 * 1024,
            )
            candidates, error = await self._plugin_part(source, previous)
            system = await self._system_part(source, previous)
            system = self._scope_system(system)
            report = CheckReport(
                checked_at=datetime.now(UTC),
                candidates=candidates,
                error=error,
                system=system,
            )
            try:
                self._cache.save(report)
            except OSError as exc:  # metrics and events must not depend on the disk
                logger.warning(
                    "plugin_update_cache_write_failed: %s", type(exc).__name__
                )
            served = self._present(report) or report
            self._last = (time.monotonic(), report)
            publish_update_metrics(served)
            if error is None:
                await self._announce(report)
            elif system is not None:
                await self._announce_system(system)
            return served

    async def _plugin_part(
        self, source: GitHubReleaseSource, previous: CheckReport | None
    ) -> tuple[list[UpdateCandidate], str | None]:
        if not self._config.plugin_checks_enabled:
            return [], None
        try:
            report = await self._run(source)
        except Exception as exc:
            logger.warning("plugin_update_check_failed: %s", type(exc).__name__)
            carried = self._current_trust(previous.candidates) if previous else []
            return carried, _safe_error(exc)
        return report.candidates, report.error

    async def _system_part(
        self, source: GitHubReleaseSource, previous: CheckReport | None
    ) -> SystemUpdate | None:
        if not self._config.system_checks_enabled:
            return None
        slug = self._config.core_update_repo.strip()
        prior = previous.system if previous else None
        try:
            return await check_system(CORE_VERSION, slug, source=source, previous=prior)
        except Exception as exc:
            logger.warning("system_update_check_failed: %s", type(exc).__name__)
            bare = SystemUpdate(
                repo=slug, installed_version=CORE_VERSION, error=_safe_error(exc)
            )
            return carry_over(bare, prior, releases=True, advisories=True)

    async def _run(self, source: GitHubReleaseSource) -> CheckReport:
        sources_file = self._config.sources_file
        if sources_file is None:
            raise RuntimeError("no sources file configured")
        sources = load_sources(sources_file)
        trust = self._config.trust
        return await run_check(
            sources,
            installed_versions(self._bundled_root),
            source=source,
            cache=self._cache,
            core_version=CORE_VERSION,
            trusted_keys=load_trust_roots,
            trust=trust,
        )

    async def _emit(self, name: str, data: dict[str, object]) -> bool:
        try:
            await get_event_bus().emit(name, data, source="plugin_updates")
        except Exception as exc:
            logger.warning("plugin_update_event_failed: %s", type(exc).__name__)
            return False
        return True

    async def _announce_once(
        self, key: str, name: str, data: dict[str, object]
    ) -> bool:
        """Emit ``name`` unless another process did; True once the key is settled.

        A claim whose emit fails is released, so the next check retries.
        """
        if not await self._gate.claim(key):
            return await self._gate.is_done(key)
        if await self._emit(name, data):
            await self._gate.commit(key)
            return True
        await self._gate.release(key)
        return False

    def _save_notified(self, notified: set[str]) -> None:
        try:
            self._cache.save_notified(notified)
        except OSError as exc:
            logger.warning("plugin_update_cache_write_failed: %s", type(exc).__name__)

    async def _announce_system(self, system: SystemUpdate) -> None:
        version = system.latest.version if system.latest else None
        key = f"system:{version}"
        notified = self._cache.load_notified()
        if not system.available or version is None or key in notified:
            return
        settled = await self._announce_once(
            key,
            SYSTEM_EVENT_NAME,
            {
                "component": system.component,
                "repo": system.repo,
                "installed_version": system.installed_version,
                "latest_version": version,
                "behind": system.behind,
                "major": system.major,
                "security": system.security,
                "severity": system.severity,
            },
        )
        if settled:
            notified.add(key)
            self._save_notified(notified)

    async def _announce(self, report: CheckReport) -> None:
        notified = self._cache.load_notified()
        fresh = []
        for cand in report.candidates:
            version = cand.latest.version if cand.latest else None
            key = f"{cand.plugin}@{version or ''}"
            if cand.available and key not in notified:
                fresh.append((key, cand, version))
        changed = False
        for key, cand, version in fresh:
            settled = await self._announce_once(
                key,
                EVENT_NAME,
                {
                    "plugin": cand.plugin,
                    "installed_version": cand.installed_version,
                    "latest_version": version,
                },
            )
            if settled:
                notified.add(key)
                changed = True
        if changed:
            self._save_notified(notified)
        if report.system is not None:
            await self._announce_system(report.system)

    async def _loop(self) -> None:
        await asyncio.sleep(FIRST_RUN_DELAY_SECONDS)
        while True:
            try:
                await self.check_now()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("plugin_update_loop_error: %s", type(exc).__name__)
            await asyncio.sleep(self._interval)

    async def start(self) -> None:
        """Start the periodic check (idempotent)."""
        if self._task is None or self._task.done():
            try:
                self._cache.purge_legacy()
            except OSError as exc:  # never installable: tarball_path ignores it
                logger.warning(
                    "plugin_update_cache_purge_failed: %s", type(exc).__name__
                )
            # A restart must not blank the alert until the first check lands.
            publish_update_metrics(self.report())
            self._task = asyncio.create_task(self._loop(), name="plugin-updates")

    async def stop(self) -> None:
        """Cancel the periodic check and wait for it."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


_service: PluginUpdateService | None = None


def get_plugin_update_service() -> PluginUpdateService | None:
    """The running service, or None when updates are not configured."""
    return _service


def set_plugin_update_service(svc: PluginUpdateService | None) -> None:
    """Install (or clear) the process-wide service."""
    global _service
    _service = svc


__all__ = [
    "CHECK_COOLDOWN_SECONDS",
    "PluginUpdateService",
    "get_plugin_update_service",
    "set_plugin_update_service",
]
