"""Plugin update service: cached report, on-demand check and periodic loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from baselith.core import _version
from baselith.core._core_version import CORE_VERSION
from baselith.core._version import __version__ as FRAMEWORK_VERSION
from baselith.core.config.plugin_update_apply import (
    UpdateApplyConfig,
    get_update_apply_config,
)
from baselith.core.config.plugin_updates import PluginUpdateConfig
from baselith.core.config.plugins import get_plugin_config
from baselith.core.events import get_event_bus
from baselith.core.plugins.signing import load_trust_roots

from ._announcing import EVENT_NAME, SYSTEM_EVENT_NAME, AnnouncingMixin
from ._preflight_live import live_preflight
from ._schedule import run_periodic
from ._system_notice import scope_notice
from .announce import AnnouncementGate, build_announcement_gate
from .apply.eligibility import already_reached, apply_status
from .apply.models import UpdaterHeartbeat
from .apply.store import RunStore
from .cache import UpdateCache
from .checker import installed_versions, run_check
from .distribution import (
    distribution_name,
    distribution_part,
    installed_distribution_version,
    scope_distribution,
)
from .metrics import publish_update_metrics
from .models import CheckReport, SystemUpdate, UpdateCandidate
from .preflight import CoreState, PreflightItem
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


class PluginUpdateService(AnnouncingMixin):
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

    def preflight(
        self, subject: str, version: str | None = None, core: CoreState | None = None
    ) -> list[PreflightItem]:
        """The pre-flight of installing ``subject`` here now (blocking reads).

        ``core`` means the distribution's own release in a distribution and
        the public core release otherwise; ``core`` (the state) carries a
        release verified by the caller. See :mod:`~.preflight`.
        """
        try:
            method = self.deployment().method
        except Exception as exc:  # unknown shape: judge it as not a host install
            self._warn_once("preflight_deployment_failed", exc)
            method = "helm"
        return live_preflight(
            subject,
            version,
            report=self.report(),
            config=self._apply_config,
            method=method,
            bundled_root=self._bundled_root,
            core_version=CORE_VERSION,
            distribution=distribution_name() is not None,
            store=self._runs,
            core=core,
        )

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
        return report.model_copy(
            update={
                "system": system,
                "distribution": self._scope_distribution(report.distribution),
                "candidates": candidates,
            }
        )

    def _scope_distribution(self, notice: SystemUpdate | None) -> SystemUpdate | None:
        installed = installed_distribution_version()
        return scope_distribution(notice, self._config, installed)

    def _scope_system(self, system: SystemUpdate | None) -> SystemUpdate | None:
        """Serve only a notice about this core, with today's guide link.

        See :func:`~._system_notice.scope_notice`: a notice about another
        repository or installed version, or one saved while the check was on
        and read after it was switched off, is dropped.
        """
        return scope_notice(
            system,
            component="core",
            enabled=self._config.system_checks_enabled,
            repo=self._config.core_update_repo.strip(),
            installed=CORE_VERSION,
            guide_url=self._config.upgrade_guide_url,
        )

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
                    update={
                        "system": self._scope_system(previous.system),
                        "distribution": self._scope_distribution(previous.distribution),
                    }
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
                distribution=await distribution_part(self._config, source, previous),
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
            else:
                await self._announce_notices(report)
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

    async def _loop(self) -> None:
        await run_periodic(
            self.check_now,
            self._cache,
            self._interval,
            first_delay=FIRST_RUN_DELAY_SECONDS,
            on_reuse=lambda: publish_update_metrics(self.report()),
        )

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
    "EVENT_NAME",
    "SYSTEM_EVENT_NAME",
    "PluginUpdateService",
    "get_plugin_update_service",
    "set_plugin_update_service",
]
