"""Client-side caching of cacheable MCP results.

Servers hand out ``ttlMs`` and ``cacheScope`` on the list and read operations;
honouring them is what turns a chatty client into a quiet one. The TTL is a
*freshness hint*, checked when the data is needed — never a polling timer, and
never a reason to refetch in the background.

The cache key is the method plus the parameters that shape the result, so a
different cursor or URI is a different entry. Two things are deliberately never
cached: results produced by a multi round-trip retry, which depend on inputs
outside the key, and anything from a request carrying per-request state.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

# Operations whose results carry caching hints.
CACHEABLE_METHODS = frozenset(
    {
        "server/discover",
        "tools/list",
        "prompts/list",
        "resources/list",
        "resources/templates/list",
        "resources/read",
    }
)

# A notification invalidates the listings it speaks for.
INVALIDATED_BY = {
    "notifications/tools/list_changed": ("tools/list",),
    "notifications/prompts/list_changed": ("prompts/list",),
    "notifications/resources/list_changed": (
        "resources/list",
        "resources/templates/list",
    ),
}


@dataclass
class _Entry:
    result: dict[str, Any]
    expires_at: float
    scope: str


def cache_key(method: str, params: dict[str, Any]) -> str:
    """Stable key for *method* and the params that affect its result.

    ``_meta`` is excluded: protocol metadata identifies the request, not the
    answer, so two otherwise identical calls must share one entry.
    """
    salient = {k: v for k, v in params.items() if k != "_meta"}
    return json.dumps([method, salient], sort_keys=True, separators=(",", ":"))


def is_cacheable(method: str, params: dict[str, Any], result: dict[str, Any]) -> bool:
    """Whether *result* may be stored.

    Interim results are excluded (they are not final), and so is anything
    produced with ``inputResponses``/``requestState``, whose inputs are not
    part of the key and would otherwise be served to a request that never
    supplied them.
    """
    if method not in CACHEABLE_METHODS:
        return False
    if result.get("resultType") not in (None, "complete"):
        return False
    if "inputResponses" in params or "requestState" in params:
        return False
    return isinstance(result.get("ttlMs"), int) and result["ttlMs"] > 0


class ResultCache:
    """TTL cache for one client's view of one server."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    def get(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Return the fresh cached result, or None when absent or stale."""
        entry = self._entries.get(cache_key(method, params))
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            self._entries.pop(cache_key(method, params), None)
            return None
        return entry.result

    def store(
        self, method: str, params: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Cache *result* when the server said it may be cached."""
        if not is_cacheable(method, params, result):
            return
        self._entries[cache_key(method, params)] = _Entry(
            result=result,
            expires_at=time.monotonic() + result["ttlMs"] / 1000,
            scope=result.get("cacheScope", "private"),
        )

    def invalidate(self, notification_method: str) -> None:
        """Drop the entries a change notification makes stale immediately."""
        methods = INVALIDATED_BY.get(notification_method)
        if not methods:
            return
        for key in [k for k in self._entries if json.loads(k)[0] in methods]:
            del self._entries[key]
        logger.debug("mcp_cache_invalidated", notification=notification_method)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


__all__ = ["CACHEABLE_METHODS", "INVALIDATED_BY", "ResultCache", "cache_key"]
