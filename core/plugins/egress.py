"""Per-plugin outbound egress control, wired into the SSRF choke point.

Every outbound request a plugin makes through the hardened HTTP client already
passes :mod:`core.security.ssrf`, which refuses internal and link-local targets.
That guard answers "is this address safe to reach?" — not "is *this plugin*
allowed to reach it". A plugin compromised through a supply-chain update, or one
that simply does more than its README claims, can exfiltrate to any public host.

This module closes that gap by installing an egress guard into the SSRF layer.
The layer itself stays free of plugin knowledge: it calls a registered callable
with the target host, and that callable decides.

The decision sequence — no plugin bound, ``off``, undeclared, then the
declaration — lives in :mod:`core.plugins.permission_runtime` and is shared with
the tool and secret guards in :mod:`core.plugins.access`.
"""

from __future__ import annotations

from core.observability.logging import get_logger
from core.plugins.permission_runtime import (
    Decision,
    PermissionsResolver,
    decide,
    set_permissions_resolver,
)
from core.security.ssrf import SsrfError, set_egress_guard

logger = get_logger(__name__)

__all__ = [
    "EgressNotPermittedError",
    "PermissionsResolver",
    "install_egress_guard",
    "set_permissions_resolver",
    "uninstall_egress_guard",
]

#: Reported once per (plugin, host) so a chatty integration cannot flood the log.
_WARNED: set[tuple[str, str]] = set()


class EgressNotPermittedError(SsrfError):
    """A plugin tried to reach a host outside its declared egress set.

    Subclasses :class:`~core.security.ssrf.SsrfError` so every caller that
    already handles a refused outbound target handles this too, without a new
    exception type leaking into unrelated code.
    """

    def __init__(self, plugin: str, host: str) -> None:
        super().__init__(
            f"Plugin {plugin!r} is not permitted to reach {host!r}: add it to "
            "the manifest's permissions.network.egress list"
        )
        self.plugin = plugin
        self.host = host


def _guard(host: str) -> None:
    """Decide whether the bound plugin may reach ``host``."""
    decision, plugin, mode = decide(lambda permissions: permissions.allows_host(host))
    if decision is Decision.ALLOW:
        return

    if decision is Decision.DENY:
        logger.warning(
            "plugin_egress_denied",
            extra={"plugin": plugin, "host": host, "mode": mode.value},
        )
        raise EgressNotPermittedError(plugin, host)

    key = (plugin, host)
    if key not in _WARNED:
        _WARNED.add(key)
        logger.warning(
            "plugin_egress_undeclared",
            extra={
                "plugin": plugin,
                "host": host,
                "mode": mode.value,
                "hint": "add it to permissions.network.egress, or set "
                "BASELITH_PLUGIN_PERMISSIONS=enforce to refuse it",
            },
        )


def install_egress_guard() -> None:
    """Route SSRF host screening through the per-plugin egress decision."""
    set_egress_guard(_guard)


def uninstall_egress_guard() -> None:
    """Remove the guard and forget what has been warned about."""
    set_egress_guard(None)
    _WARNED.clear()
