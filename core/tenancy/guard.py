"""
Cross-tenant access guards.

A reusable enforcement layer for the invariant "a request may only touch its own
tenant's resources". Stores and services resolve a resource's ``tenant_id`` and
check it against the request's active tenant context before acting — this
generalizes the ad-hoc check each store would otherwise reimplement (and which,
when forgotten, becomes a cross-tenant IDOR).

Two shapes are provided:

* :func:`tenants_match` — a boolean predicate, for call sites that prefer to
  treat a mismatch as *not found* (avoids leaking that a resource exists).
* :func:`require_tenant_match` — raises :class:`CrossTenantError`, for choke
  points that should fail loudly.
"""

from __future__ import annotations

from core.context import get_current_tenant_id


class CrossTenantError(Exception):
    """A resource belonging to another tenant was accessed."""

    def __init__(self, resource_tenant: str, current_tenant: str) -> None:
        super().__init__(
            "Cross-tenant access denied "
            f"(resource={resource_tenant!r}, current={current_tenant!r})"
        )
        self.resource_tenant = resource_tenant
        self.current_tenant = current_tenant


def tenants_match(resource_tenant: str, current: str | None = None) -> bool:
    """Return whether ``resource_tenant`` is the request's active tenant.

    Args:
        resource_tenant: The tenant that owns the resource.
        current: The tenant to check against; defaults to the active tenant
            context (:func:`core.context.get_current_tenant_id`).
    """
    effective = current if current is not None else get_current_tenant_id()
    return resource_tenant == effective


def require_tenant_match(resource_tenant: str, *, current: str | None = None) -> None:
    """Raise :class:`CrossTenantError` unless ``resource_tenant`` is the current tenant."""
    effective = current if current is not None else get_current_tenant_id()
    if resource_tenant != effective:
        raise CrossTenantError(resource_tenant, effective)


def require_tenant_context() -> str:
    """Return the active tenant id, enforcing strict isolation if configured.

    Delegates to :func:`core.context.get_current_tenant_id`, which raises
    ``TenantContextError`` when strict isolation is on and no tenant is bound.
    """
    return get_current_tenant_id()
