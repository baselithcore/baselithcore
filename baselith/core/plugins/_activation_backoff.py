"""Failure backoff for lazy (request-driven) plugin activation.

A lazy plugin is activated by the first request to its router prefix — a path
reachable without authentication (``PluginActivationMiddleware`` runs before
any route dependency). When activation *fails*, nothing used to remember it:
every following request to the prefix re-hashed the plugin, re-imported it and
re-ran its initialisation under the global activation and reload locks. One
anonymous client looping on a broken plugin's URL kept those locks busy.

:class:`ActivationBackoff` remembers a failure for :data:`ACTIVATION_BACKOFF_SECONDS`;
within that window the runtime activator raises
:class:`PluginActivationBackoffError` instead of retrying, and the middleware
answers ``503`` with ``Retry-After`` from that alone. An operator enable (the
plugin-management API goes straight to the hot-reload controller) never
consults the backoff, and a successful activation clears it.
"""

from __future__ import annotations

import math
import time

#: How long a failed lazy activation is not retried by request traffic.
ACTIVATION_BACKOFF_SECONDS = 60.0


class PluginActivationBackoffError(RuntimeError):
    """A recent activation of this plugin failed; not retried until the backoff ends.

    Subclasses :class:`RuntimeError` so callers that already treated a failed
    lazy activation as a ``RuntimeError`` (the flow-handler proxy) keep doing so.

    Attributes:
        plugin_name: The plugin whose activation is backed off.
        retry_after: Whole seconds until request traffic may retry it (>= 1).
    """

    def __init__(self, plugin_name: str, retry_after: int) -> None:
        super().__init__(
            f"Plugin '{plugin_name}' failed to activate recently; "
            f"retry in {retry_after}s."
        )
        self.plugin_name = plugin_name
        self.retry_after = retry_after


class ActivationBackoff:
    """Per-plugin deadlines before which lazy activation is not re-attempted."""

    def __init__(self, seconds: float = ACTIVATION_BACKOFF_SECONDS) -> None:
        self._seconds = seconds
        self._until: dict[str, float] = {}

    def check(self, plugin_name: str) -> None:
        """Raise :class:`PluginActivationBackoffError` while backed off."""
        until = self._until.get(plugin_name)
        if until is None:
            return
        remaining = until - time.monotonic()
        if remaining <= 0:
            self._until.pop(plugin_name, None)
            return
        raise PluginActivationBackoffError(plugin_name, max(1, math.ceil(remaining)))

    def record_failure(self, plugin_name: str) -> None:
        """Start (or restart) the backoff window for ``plugin_name``."""
        self._until[plugin_name] = time.monotonic() + self._seconds

    def clear(self, plugin_name: str | None = None) -> None:
        """Forget one plugin's failure, or every failure when ``None``."""
        if plugin_name is None:
            self._until.clear()
        else:
            self._until.pop(plugin_name, None)


__all__ = [
    "ACTIVATION_BACKOFF_SECONDS",
    "ActivationBackoff",
    "PluginActivationBackoffError",
]
