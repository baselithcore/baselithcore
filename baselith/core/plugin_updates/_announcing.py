"""Announcing new updates on the event bus, once per release across processes.

Each new release is announced once, under a settled key: ``<plugin>@<version>``
for a plugin, ``system:<version>`` for the core and ``distribution:<version>``
for a distribution, so equal version strings never suppress each other. The
:class:`~.announce.AnnouncementGate` keeps several workers from announcing
the same key; the cache's notified set keeps a restart from announcing again.
"""

from __future__ import annotations

import logging

from ._system_notice import notice_announcement
from .announce import AnnouncementGate
from .cache import UpdateCache
from .distribution import NOTIFIED_KEY_PREFIX
from .models import CheckReport, SystemUpdate

logger = logging.getLogger(__name__)

EVENT_NAME = "plugin.update_available"
SYSTEM_EVENT_NAME = "system.update_available"


class AnnouncingMixin:
    """The announcement half of :class:`~.service.PluginUpdateService`."""

    _cache: UpdateCache
    _gate: AnnouncementGate

    async def _emit(self, name: str, data: dict[str, object]) -> bool:
        """Emit one event; False when the bus failed (implemented by the service)."""
        raise NotImplementedError

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

    async def _announce_system(
        self, system: SystemUpdate, prefix: str = "system"
    ) -> None:
        """Announce ``system`` once per ``<prefix>:<version>`` across processes."""
        announcement = notice_announcement(system, notified_key_prefix=prefix)
        notified = self._cache.load_notified()
        if announcement is None or announcement[0] in notified:
            return
        key, payload = announcement
        if await self._announce_once(key, SYSTEM_EVENT_NAME, payload):
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
        await self._announce_notices(report)

    async def _announce_notices(self, report: CheckReport) -> None:
        """Announce the core notice, then the distribution's (distinct keys)."""
        if report.system is not None:
            await self._announce_system(report.system)
        if report.distribution is not None:
            await self._announce_system(report.distribution, NOTIFIED_KEY_PREFIX)


__all__ = ["EVENT_NAME", "SYSTEM_EVENT_NAME", "AnnouncingMixin"]
