"""
Context management for the BaselithCore framework.

This module provides thread-safe and async-safe context propagation using
Python's `contextvars`. It is primarily used to track user/tenant identity
across asynchronous call stacks without passing it explicitly through every function.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable

from core.config import get_app_config


class TenantContextError(Exception):
    """
    Raised when a tenant-specific operation is attempted but no tenant ID is set,
    and the application is configured with `strict_tenant_isolation=True`.
    """

    pass


# Global context variable for the current tenant ID.
# ContextVar ensures that each async task or thread has its own isolated value.
_tenant_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tenant_context", default=None
)

# Global context variable for the current authenticated user id.
# Bound at the same chokepoints as the tenant (the auth guards / security &
# tenant middleware), so it is identity-derived — never a client-supplied
# value. Lets a plugin resolve a *per-user* tenant (1 user = 1 tenant) even when
# the deployment-level tenant is shared. See :func:`resolve_plugin_tenant`.
_user_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
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


#: Tenant ids the framework reserves for itself. ``system`` is the identity
#: :func:`core.db.connection.system_tenant_scope` binds for maintenance work,
#: and migration ``010_system_tenant_rls_exemption`` grants it visibility of
#: **every** tenant's rows. A principal that managed to carry it — a JWT claim,
#: an API-key record, a provisioning call that took the id from input — would
#: therefore read and write everything. Naming it here gives the request
#: boundary and the provisioning path one authority to check against instead of
#: a literal in each.
#:
#: Kept as a literal rather than imported from ``core.db.session_setup``: that
#: module pulls in psycopg and the app config, which this one must not.
#: ``tests/unit/core/test_reserved_tenants.py`` fails if the two drift.
RESERVED_TENANT_IDS: frozenset[str] = frozenset({"system"})


class ReservedTenantError(ValueError):
    """A reserved tenant id arrived from outside the framework.

    Raised where such an id would otherwise be *accepted* — minting a token that
    asserts it, provisioning a tenant record for it. A ``ValueError`` subclass so
    callers that already treat bad input that way keep working.
    """


def is_reserved_tenant(tenant_id: str | None) -> bool:
    """Whether *tenant_id* is a framework-reserved identity.

    Args:
        tenant_id: The candidate id — a token claim, a provisioning request
            field, anything that did not come from the framework itself.

    Returns:
        True when the id is reserved and must not be accepted from outside.
    """
    return tenant_id in RESERVED_TENANT_IDS


def bind_principal_tenant(tenant_id: str) -> contextvars.Token:
    """Bind a tenant that came from a **principal**, refusing reserved ids.

    The only correct way to bind a tenant derived from a caller's identity — a
    JWT or OIDC claim, an API-key record, anything a request, connection or
    token asserted. Use it at every such site; use the plain
    :func:`set_tenant_context` only for a value the framework already owns (the
    maintenance identity via :func:`core.db.connection.system_tenant_scope`, a
    job's enqueued metadata, an event record, a checkpoint's stored tenant).

    The distinction is not stylistic. ``system`` is what maintenance work binds,
    and migration ``010_system_tenant_rls_exemption`` grants it visibility of
    every tenant's rows, so a principal that carried it would read and write
    everything. Tokens asserting it cannot be minted here
    (:mod:`core.auth._jwt_issue`), but an external issuer — an OIDC directory
    mapping a group onto the tenant claim — is outside this repository's reach,
    and so is any deployment that adds its own authenticating entry point. A
    per-site ``if is_reserved_tenant(...)`` check only protects the sites
    somebody remembered; making the *binding call itself* refuse protects the
    ones nobody thought of, including the ones in another checkout that shares
    this ``core``.

    Args:
        tenant_id: The tenant the principal asserts.

    Returns:
        contextvars.Token: token to restore the previous value via
        :func:`reset_tenant_context`, exactly like :func:`set_tenant_context`.

    Raises:
        ReservedTenantError: The id is framework-reserved. Callers translate
            this into their own refusal — a 403 for an HTTP request, a closed
            socket for a connection — and must never fall back to binding it.
    """
    if is_reserved_tenant(tenant_id):
        raise ReservedTenantError(
            f"'{tenant_id}' is a reserved tenant identifier and cannot be bound "
            "from a principal. It belongs to the framework's own maintenance "
            "context."
        )
    return _tenant_context.set(tenant_id)


def tenant_is_bound() -> bool:
    """Whether a tenant is bound to the current execution context.

    The public answer to "did someone upstream actually say which tenant this
    work belongs to?", as opposed to :func:`get_current_tenant_id`, which
    conflates *unbound* with the ``"default"`` fallback unless
    ``strict_tenant_isolation`` happens to be on. Fail-closed callers that must
    refuse unattributed work regardless of that unrelated switch — row-level
    security binding the DB session, for one — ask this instead of reaching
    into the module-private contextvar.

    Returns:
        True when a tenant id is bound to this context, False otherwise.
    """
    return _tenant_context.get() is not None


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


def get_current_user_id() -> str | None:
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


# Global context variable for the plugin the current execution runs on behalf
# of. Bound at framework chokepoints — the plugin-context HTTP middleware (a
# request hitting a plugin's routes) and the orchestrator's handler dispatch
# (an intent owned by a plugin) — never by the plugin itself, so downstream
# seams (e.g. the central per-plugin LLM policy) can trust it. ``None`` means
# "not attributable to a plugin" (core routes, background tasks, scripts).
_plugin_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "plugin_context", default=None
)


def get_current_plugin() -> str | None:
    """Return the plugin the current context executes on behalf of, if any.

    Bound by the plugin-context middleware (HTTP requests routed to a plugin)
    and the orchestrator dispatch (intents owned by a plugin). Returns ``None``
    when execution is not attributable to a plugin — core routes, background
    tasks, scripts. Never raises: callers decide how to degrade.
    """
    return _plugin_context.get()


def set_plugin_context(plugin_name: str) -> contextvars.Token:
    """Bind the active plugin for the current execution context.

    Framework-internal: called by the plugin-context middleware and the
    orchestrator dispatch. Plugins must not self-bind (a plugin claiming
    another's identity would inherit its central LLM policy).

    Args:
        plugin_name: The plugin's manifest ``name``.

    Returns:
        contextvars.Token: token to restore the previous value via
        :func:`reset_plugin_context`.
    """
    return _plugin_context.set(plugin_name)


def reset_plugin_context(token: contextvars.Token) -> None:
    """Restore the plugin context to the state before the matching
    :func:`set_plugin_context`."""
    _plugin_context.reset(token)


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
# is domain-specific (it lives in the consuming application or plugin, e.g. an
# admin store), so core only exposes a registration seam and never imports the plugin — keeping the
# Sacred-Core boundary intact. When no resolver is registered the declared mode
# is used verbatim, so behaviour is identical to a deployment without overrides.
_PluginTenancyResolver = Callable[[str], str | None]
_plugin_tenancy_resolver: _PluginTenancyResolver | None = None


def set_plugin_tenancy_resolver(resolver: _PluginTenancyResolver | None) -> None:
    """Register (or clear, with ``None``) the per-plugin tenancy-mode override.

    The consuming application or plugin installs this (typically at
    activation). ``resolver(plugin_name)``
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
    except Exception:
        return declared_mode
    if override in (TENANCY_SHARED, TENANCY_PERSONAL):
        return override  # type: ignore[return-value]
    return declared_mode


def resolve_plugin_tenant_key(
    plugin_name: str, declared_mode: str = TENANCY_SHARED
) -> str:
    """Scope key for a plugin's storage from store-layer code (no ``Plugin`` self).

    The store-layer counterpart of
    :meth:`core.plugins.interface.Plugin.tenant_key`: resolves the plugin's
    effective tenancy mode — honouring a runtime admin override of the
    manifest-declared ``declared_mode`` — and maps it to the identity-derived
    tenant. Use this instead of :func:`get_current_tenant_id` wherever a plugin
    scopes persistence but has no ``self`` to call ``tenant_key()`` on, so the
    store honours per-plugin tenancy overrides too. For ``shared`` (the default)
    with no override it is exactly :func:`get_tenant_or_default`, so swapping it
    in is behaviour-preserving.

    Note: the ``system`` plugin override-exemption lives at the ``Plugin``
    chokepoint; this helper does not re-check it (a store belongs to a
    non-system plugin), so system plugins must not use it to bypass that.

    Args:
        plugin_name: The plugin's manifest ``name`` (the override key).
        declared_mode: The plugin's manifest-declared tenancy mode.

    Returns:
        The tenant id the plugin should scope its storage by.
    """
    return resolve_plugin_tenant(
        resolve_plugin_tenancy_mode(plugin_name, declared_mode)
    )
