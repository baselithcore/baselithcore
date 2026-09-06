"""Shared runtime for the declared-permission checks.

Three chokepoints consult a plugin's ``permissions:`` block — outbound egress
(:mod:`core.plugins.egress`), tool invocation and secret reads
(:mod:`core.plugins.access`). They ask the same three questions in the same
order, and getting that order wrong in one of them is how a staged rollout
starts denying traffic it promised not to:

1. Is a plugin bound to this call at all? Core traffic is never gated.
2. What mode is the deployment in? ``off`` never consults a declaration.
3. Did this plugin *declare* anything? One that did not is not migrated yet,
   and is never refused — that is what makes ``enforce`` safe to switch on.

Only after all three does the declaration decide. This module owns that
sequence once, as :func:`decide`, so the three guards cannot drift apart.

It also owns the resolver seam: the lookup from a plugin name to its parsed
permissions is *installed* by the app lifespan rather than imported, so no
guard depends on registry load order and a test can drive one directly.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from core.context import get_current_plugin
from core.observability.logging import get_logger
from core.plugins.permissions import (
    PermissionMode,
    PluginPermissions,
    resolve_permission_mode,
)

logger = get_logger(__name__)

__all__ = [
    "Decision",
    "PermissionsResolver",
    "decide",
    "permissions_for",
    "set_permissions_resolver",
]

#: Lookup from plugin name to its declared permissions.
PermissionsResolver = Callable[[str], "PluginPermissions | None"]
_RESOLVER: PermissionsResolver | None = None


class Decision(str, Enum):
    """What a guard should do with one call."""

    ALLOW = "allow"
    #: Outside the declaration, but the mode only observes: proceed and report.
    WARN = "warn"
    DENY = "deny"


def set_permissions_resolver(resolver: PermissionsResolver | None) -> None:
    """Install the lookup from plugin name to its declared permissions.

    Args:
        resolver: Callable taking a plugin name and returning its
            :class:`~core.plugins.permissions.PluginPermissions`, or ``None``
            when the plugin is unknown. Pass ``None`` to clear.
    """
    global _RESOLVER
    _RESOLVER = resolver


def permissions_for(plugin: str) -> PluginPermissions | None:
    """The declared permissions of ``plugin``, or ``None`` when unknown.

    A resolver that raises yields ``None``: a broken lookup must not break the
    call path it guards, and ``None`` means "not migrated", which is the
    permissive branch everywhere.
    """
    if _RESOLVER is None:
        return None
    try:
        return _RESOLVER(plugin)
    except Exception:
        logger.debug("plugin_permissions_lookup_failed", exc_info=True)
        return None


def decide(
    allows: Callable[[PluginPermissions], bool],
) -> tuple[Decision, str, PermissionMode]:
    """Resolve one guarded call against the bound plugin's declaration.

    Args:
        allows: Predicate answering whether the declaration covers this call.
            Called only once the three preconditions above hold, so it never
            has to reason about them itself.

    Returns:
        ``(decision, plugin_name, mode)``. ``plugin_name`` is empty when no
        plugin is bound, in which case the decision is always
        :attr:`Decision.ALLOW`.
    """
    plugin = get_current_plugin() or ""
    if not plugin:
        return Decision.ALLOW, "", PermissionMode.WARN

    mode = resolve_permission_mode()
    if mode is PermissionMode.OFF:
        return Decision.ALLOW, plugin, mode

    permissions = permissions_for(plugin)
    if permissions is None or not permissions.declared:
        return Decision.ALLOW, plugin, mode

    if allows(permissions):
        return Decision.ALLOW, plugin, mode
    return (Decision.DENY if mode.enforces else Decision.WARN), plugin, mode
