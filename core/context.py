"""
Context management for the BaselithCore framework.

This module provides thread-safe and async-safe context propagation using
Python's `contextvars`. It is primarily used to track user/tenant identity
across asynchronous call stacks without passing it explicitly through every function.
"""

from __future__ import annotations

import contextvars
from typing import Callable, Optional

from core.config import get_app_config


class TenantContextError(Exception):
    """
    Raised when a tenant-specific operation is attempted but no tenant ID is set,
    and the application is configured with `strict_tenant_isolation=True`.
    """

    pass


# Global context variable for the current tenant ID.
# ContextVar ensures that each async task or thread has its own isolated value.
_tenant_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "tenant_context", default=None
)

# Global context variable for the current authenticated user id.
# Bound at the same chokepoints as the tenant (the auth guards / security &
# tenant middleware), so it is identity-derived — never a client-supplied
# value. Lets a plugin resolve a *per-user* tenant (1 user = 1 tenant) even when
# the deployment-level tenant is shared. See :func:`resolve_plugin_tenant`.
_user_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "user_context", default=None
)


def get_current_tenant_id() -> str:
    """
    Retrieve the tenant ID associated with the current execution context.

    If no tenant context is set via `set_tenant_context`, it returns "default"
    unless `strict_tenant_isolation` is enabled in the app configuration,
    in which case it raises a `TenantContextError`.

    Returns:
        str: The current tenant ID.

    Raises:
        TenantContextError: If strict isolation is enabled and no context is set.
    """
    tenant_id = _tenant_context.get()
    if tenant_id is None:
        # Fallback for when context is not set (e.g., background tasks, scripts)
        if get_app_config().strict_tenant_isolation:
            raise TenantContextError(
                "Strict tenant isolation enabled: No tenant context found in current contextvar."
            )
        return "default"
    return tenant_id


def get_tenant_or_default() -> str:
    """Like :func:`get_current_tenant_id` but never raises.

    Returns the active tenant, or ``"default"`` when no tenant context is bound
    — even under ``strict_tenant_isolation``. The canonical way for a plugin's
    persistence to scope rows by tenant without breaking out-of-request callers
    (background tasks, scripts, schema bootstrap), where ``"default"`` matches
    the pre-tenant behaviour and keeps existing single-tenant data reachable.
    """
    try:
        return get_current_tenant_id()
    except TenantContextError:
        return "default"


def set_tenant_context(tenant_id: str) -> contextvars.Token:
    """
    Set the tenant ID for the current execution context.

    This should typically be called at the entry point of a request or task
    (e.g., in a FastAPI middleware or task worker).

    Args:
        tenant_id: The ID of the tenant to associate with this context.

    Returns:
        contextvars.Token: A token used to restore the previous context via `reset_tenant_context`.
    """
    return _tenant_context.set(tenant_id)


def reset_tenant_context(token: contextvars.Token) -> None:
    """
    Restore the tenant context to the state before the corresponding `set_tenant_context`.

    Args:
        token: The token returned by `set_tenant_context`.
    """
    _tenant_context.reset(token)


def get_current_user_id() -> Optional[str]:
    """Return the authenticated user id bound to the current context, if any.

    Bound at the same chokepoints as the tenant context (auth guards, security
    & tenant middleware). Returns ``None`` when no user is bound — e.g. an
    unauthenticated request, a background task, or a script. Never raises:
    callers decide how to degrade (see :func:`resolve_plugin_tenant`).
    """
    return _user_context.get()


def set_user_context(user_id: str) -> contextvars.Token:
    """Bind the authenticated user id for the current execution context.

    Call alongside :func:`set_tenant_context` at the request/task entry point.

    Args:
        user_id: The authenticated user's identifier.

    Returns:
        contextvars.Token: token to restore the previous value via
        :func:`reset_user_context`.
    """
    return _user_context.set(user_id)


def reset_user_context(token: contextvars.Token) -> None:
    """Restore the user context to the state before the matching
    :func:`set_user_context`."""
    _user_context.reset(token)


# Per-plugin tenancy modes. A plugin declares its mode in ``manifest.yaml``
# (``tenancy: personal|shared``) and resolves its effective scope key via
# :func:`resolve_plugin_tenant`. ``shared`` (default) keeps the existing,
# deployment-derived tenant; ``personal`` forces 1 user = 1 tenant regardless
# of how the deployment resolves tenancy.
TENANCY_SHARED = "shared"
TENANCY_PERSONAL = "personal"


def resolve_plugin_tenant(mode: str) -> str:
    """Resolve the effective tenant key for a plugin given its tenancy *mode*.

    This is what lets a single deployment mix tenancy models per plugin while
    staying **identity-derived** — the per-user key comes from the bound user
    context, never a request header.

    - ``"personal"`` → the authenticated user's id (1 user = 1 tenant),
      independent of the deployment-level tenant. Falls back to the
      session/default tenant when no user is bound (background task, script),
      so out-of-request callers still get a stable, non-raising key.
    - anything else (``"shared"``, the default) → the deployment-derived
      tenant from :func:`get_current_tenant_id`, via the non-raising
      :func:`get_tenant_or_default`.

    Args:
        mode: The plugin's declared tenancy mode.

    Returns:
        The tenant id the plugin should scope its storage by.
    """
    if mode == TENANCY_PERSONAL:
        user_id = get_current_user_id()
        if user_id:
            return user_id
    return get_tenant_or_default()


# Optional runtime override of a plugin's *declared* tenancy mode. A plugin may
# ship ``tenancy: shared`` in its manifest, yet an operator may need to flip it
# to ``personal`` (or back) at runtime without re-packaging. The override source
# is domain-specific (it lives in the ``auth`` plugin's admin store), so core
# only exposes a registration seam and never imports the plugin — keeping the
# Sacred-Core boundary intact. When no resolver is registered the declared mode
# is used verbatim, so behaviour is identical to a deployment without overrides.
_PluginTenancyResolver = Callable[[str], Optional[str]]
_plugin_tenancy_resolver: Optional[_PluginTenancyResolver] = None


def set_plugin_tenancy_resolver(resolver: Optional[_PluginTenancyResolver]) -> None:
    """Register (or clear, with ``None``) the per-plugin tenancy-mode override.

    The ``auth`` plugin installs this at activation. ``resolver(plugin_name)``
    returns ``"shared"`` / ``"personal"`` to override that plugin's declared
    mode, or ``None`` to inherit the manifest. It must be cheap and total
    (cached, never raising) — it is consulted on every storage scope resolution.
    """
    global _plugin_tenancy_resolver
    _plugin_tenancy_resolver = resolver


def resolve_plugin_tenancy_mode(plugin_name: str, declared_mode: str) -> str:
    """Effective tenancy mode for a plugin: a registered override, else declared.

    Degrades to ``declared_mode`` whenever no resolver is registered, the
    resolver returns ``None``/an unknown value, or it raises — so an override
    store outage can never break or silently re-scope a plugin's storage.

    Args:
        plugin_name: The plugin's manifest name (the override key).
        declared_mode: The plugin's manifest-declared tenancy mode.

    Returns:
        ``"shared"`` or ``"personal"`` — the override when valid, else declared.
    """
    resolver = _plugin_tenancy_resolver
    if resolver is None:
        return declared_mode
    try:
        override = resolver(plugin_name)
    except Exception:  # noqa: BLE001 — override is best-effort; never break scoping
        return declared_mode
    if override in (TENANCY_SHARED, TENANCY_PERSONAL):
        return override  # type: ignore[return-value]
    return declared_mode
