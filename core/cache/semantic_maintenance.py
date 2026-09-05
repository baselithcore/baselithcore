"""
Entry maintenance for the semantic cache.

TTL expiry checks, the interval-gated purge sweep and LRU eviction over the
per-tenant entry store. Mixed into
:class:`~core.cache.semantic_cache.SemanticLLMCache`; split out of
``semantic_cache.py`` to respect the module size cap. All state
(``_entries``, ``_matrix_cache``, ``_last_purge``, ``_ttl``, ``_maxsize``)
is still owned and initialised by the host class.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from core.cache.semantic_cache import CacheEntry


class EntryMaintenanceMixin:
    """Expiry + eviction bookkeeping over ``entries[tenant_id][prompt_hash]``."""

    # Provided by SemanticLLMCache.__init__.
    _entries: dict[str, dict[str, CacheEntry]]
    _matrix_cache: dict[str, tuple[list[CacheEntry], np.ndarray]]
    _last_purge: dict[str, float]
    _ttl: float
    _maxsize: int

    # Full expiry sweeps run at most this often per tenant (same cadence as
    # ``TTLCache.PURGE_INTERVAL``); per-entry read checks keep results exact.
    _PURGE_INTERVAL_SECONDS = 60.0

    def _is_expired(self, entry: CacheEntry, now: float | None = None) -> bool:
        """True when the entry's sliding TTL has elapsed."""
        return ((now if now is not None else time.time()) - entry.timestamp) > self._ttl

    def _purge_expired(self, tenant_id: str, *, force: bool = False) -> None:
        """Remove expired entries for a tenant (interval-gated unless forced)."""
        if tenant_id not in self._entries:
            return

        now = time.time()
        if (
            not force
            and (now - self._last_purge.get(tenant_id, 0.0))
            < self._PURGE_INTERVAL_SECONDS
        ):
            return
        self._last_purge[tenant_id] = now
        expired = [
            h for h, e in self._entries[tenant_id].items() if self._is_expired(e, now)
        ]
        for h in expired:
            del self._entries[tenant_id][h]
        if expired:
            self._matrix_cache.pop(tenant_id, None)

    def _evict_lru(self, tenant_id: str) -> None:
        """Evict least recently used entry for a specific tenant."""
        if tenant_id not in self._entries or not self._entries[tenant_id]:
            return

        # Find entry with oldest timestamp and lowest hits
        oldest_hash = min(
            self._entries[tenant_id].keys(),
            key=lambda k: (
                self._entries[tenant_id][k].timestamp,
                -self._entries[tenant_id][k].hits,
            ),
        )
        del self._entries[tenant_id][oldest_hash]
        self._matrix_cache.pop(tenant_id, None)


__all__ = ["EntryMaintenanceMixin"]
