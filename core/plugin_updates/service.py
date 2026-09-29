"""Plugin update service: cached report, on-demand check and periodic loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from core._version import __version__ as CORE_VERSION
from core.config.plugin_updates import PluginUpdateConfig
from core.events import get_event_bus
from core.plugins.signing import load_trusted_keys

from .cache import UpdateCache
from .checker import installed_versions, run_check
from .metrics import publish_update_metrics
from .models import CheckReport, SystemUpdate, UpdateCandidate
from .sources import GitHubReleaseSource, load_sources, safe_error
from .system import carry_over, check_system

logger = logging.getLogger(__name__)

FIRST_RUN_DELAY_SECONDS = 60
#: A check requested over the API within this many seconds of the last one
#: completed in this process returns that report instead of calling GitHub.
CHECK_COOLDOWN_SECONDS = 60.0
EVENT_NAME = "plugin.update_available"
SYSTEM_EVENT_NAME = "system.update_available"


_safe_error = safe_error


class PluginUpdateService:
    """Owns the update cache and the background check loop."""

    def __init__(
        self, config: PluginUpdateConfig, bundled_root: Path = Path("plugins")
    ) -> None:
        """Create the service; nothing runs until :meth:`start`."""
        self._config = config
        self._bundled_root = bundled_root
        self._cache = UpdateCache(config.cache_dir)
        self._interval: float = config.check_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._last: tuple[float, CheckReport] | None = None

    def report(self) -> CheckReport | None:
        """The last saved report, if any."""
        return self._cache.load()

    async def request_check(self) -> CheckReport:
        """``check_now`` for API callers, throttled per process.

        When a check completed less than :data:`CHECK_COOLDOWN_SECONDS` ago in
        this process, its report is returned and GitHub is not contacted, so a
        repeatedly pressed "Check now" cannot burn the API rate limit. The
        periodic loop calls :meth:`check_now` directly and is not throttled.
        """
        last = self._last
        if last is not None and time.monotonic() - last[0] < CHECK_COOLDOWN_SECONDS:
            return last[1]
        return await self.check_now()

    async def check_now(self) -> CheckReport:
        """Run the plugin and system checks, save and announce new updates.

        The two parts are independent: either may be disabled, and a failure in
        one never hides the other's results. On a plugin failure the previous
        candidates are kept and ``error`` set.
        """
        async with self._lock:
            previous = self._cache.load()
            source = GitHubReleaseSource(
                self._config.github_api_url,
                self._config.github_token,
                max_bytes=self._config.max_artifact_mb * 1024 * 1024,
            )
            candidates, error = await self._plugin_part(source, previous)
            system = await self._system_part(source, previous)
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
            self._last = (time.monotonic(), report)
            publish_update_metrics(report)
            if error is None:
                await self._announce(report)
            elif system is not None:
                await self._announce_system(system)
            return report

    async def _plugin_part(
        self, source: GitHubReleaseSource, previous: CheckReport | None
    ) -> tuple[list[UpdateCandidate], str | None]:
        if not self._config.plugin_checks_enabled:
            return [], None
        try:
            report = await self._run(source)
        except Exception as exc:
            logger.warning("plugin_update_check_failed: %s", type(exc).__name__)
            return (previous.candidates if previous else []), _safe_error(exc)
        return report.candidates, report.error

    async def _system_part(
        self, source: GitHubReleaseSource, previous: CheckReport | None
    ) -> SystemUpdate | None:
        if not self._config.system_checks_enabled:
            return None
        slug = self._config.system_update_repo.strip()
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
        keys = [k.public_key_hex for k in load_trusted_keys() if k.is_usable]
        return await run_check(
            sources,
            installed_versions(self._bundled_root),
            source=source,
            cache=self._cache,
            core_version=CORE_VERSION,
            trusted_keys=keys,
        )

    async def _emit(self, name: str, data: dict[str, object]) -> None:
        try:
            await get_event_bus().emit(name, data, source="plugin_updates")
        except Exception as exc:
            logger.warning("plugin_update_event_failed: %s", type(exc).__name__)

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
        await self._emit(
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
        for key, cand, version in fresh:
            await self._emit(
                EVENT_NAME,
                {
                    "plugin": cand.plugin,
                    "installed_version": cand.installed_version,
                    "latest_version": version,
                },
            )
            notified.add(key)
        if fresh:
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
            # A restart must not blank the alert until the first check lands.
            publish_update_metrics(self._cache.load())
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
