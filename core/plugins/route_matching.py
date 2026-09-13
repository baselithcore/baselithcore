"""Request-path → owning-plugin matching for the registry.

Extracted from ``registry.py`` to keep that module under the 500-line cap; the
public surface is unchanged because :class:`~core.plugins.registry.PluginRegistry`
mixes this in. The matching itself runs for every HTTP request (via
``PluginContextMiddleware``), which is why it reads an immutable pre-sorted
snapshot lock-free rather than sorting prefixes per request.
"""

from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING

from core.observability.logging import get_logger

from .lookup import RESERVED_ROUTE_SEGMENTS

if TYPE_CHECKING:
    from .resource_analyzer import PluginDiscovery

logger = get_logger(__name__)


class RouteMatchMixin:
    """Longest-prefix router-prefix matching over discovered plugins."""

    # Provided by PluginRegistry.
    _lock: RLock
    _route_snapshot: tuple[tuple[str, str], ...] | None
    _discovered_plugins: dict[str, PluginDiscovery]
    _suppressed_discovered_plugins: set[str]
    _warned_generic_prefixes: set[tuple[str, str]]

    def match_plugin_route(self, request_path: str) -> str | None:
        """Match a request path against discovered router prefixes.

        Hot path (runs for every HTTP request via PluginContextMiddleware): reads
        an immutable, pre-sorted snapshot lock-free. The snapshot is (prefix,
        plugin_name) entries ordered longest-prefix-first, so the first match wins
        without per-request sorting or lock acquisition.
        """
        snapshot = self._route_snapshot
        if snapshot is None:
            snapshot = self._rebuild_route_snapshot()
        for prefix, plugin_name in snapshot:
            if request_path == prefix or request_path.startswith(f"{prefix}/"):
                return plugin_name
        return None

    def _rebuild_route_snapshot(self) -> tuple[tuple[str, str], ...]:
        """Recompute and cache the sorted route-prefix snapshot (under lock)."""
        with self._lock:
            # Re-check inside the lock: a concurrent rebuild may have populated it.
            if self._route_snapshot is not None:
                return self._route_snapshot
            routes: list[tuple[int, str, str]] = []
            for plugin_name, discovery in self._discovered_plugins.items():
                if plugin_name in self._suppressed_discovered_plugins:
                    continue
                if not discovery.provides_routes or not discovery.router_prefix:
                    continue
                prefix = discovery.router_prefix.rstrip("/")
                # Skip prefixes too generic to identify a single plugin. A bare
                # "" / "/" (catch-all router) or "/api" matches (almost) every
                # request, so it would shadow unrelated plugin/core routes and
                # mis-attribute them to this plugin: a "/api"-prefixed plugin
                # would otherwise claim every unmatched "/api/*" request and bind
                # the wrong owner into the request's plugin context, corrupting
                # every context consumer (per-plugin model policy, tenancy
                # scoping, and any other seam keyed on the active plugin). Such a
                # prefix cannot express ownership; leave those requests
                # unattributed (None) rather than mislabelled.
                segments = [seg for seg in prefix.split("/") if seg]
                if not segments or (
                    len(segments) == 1 and segments[0] in RESERVED_ROUTE_SEGMENTS
                ):
                    warned = (plugin_name, discovery.router_prefix)
                    if warned not in self._warned_generic_prefixes:
                        self._warned_generic_prefixes.add(warned)
                        logger.warning(
                            "Ignoring route prefix %r for plugin %s: it is empty or "
                            "collides with a core/framework route namespace, so it "
                            "cannot attribute request ownership (a plugin claiming "
                            "it would bind the wrong owner into the plugin context "
                            "for core traffic). Requests under it stay unattributed; "
                            "reported once per process.",
                            discovery.router_prefix,
                            plugin_name,
                        )
                    continue
                routes.append((len(prefix), prefix, plugin_name))
            # Longest prefix first (most specific route wins); ties break on
            # plugin_name descending, matching the previous sort semantics.
            routes.sort(key=lambda r: (r[0], r[2]), reverse=True)
            snapshot = tuple((prefix, name) for _, prefix, name in routes)
            self._route_snapshot = snapshot
            return snapshot
