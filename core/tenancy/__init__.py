"""
Tenant isolation guarantees.

Reusable cross-tenant access guards plus per-tenant encryption-at-rest, layered
on the tenant context in :mod:`core.context`. Use the guards at any store or
service that resolves a resource by id, and the per-tenant encryptor for fields
that must be cryptographically isolated between tenants.
"""

from core.tenancy.encryption import (
    derive_tenant_key_material,
    tenant_field_encryptor,
)
from core.tenancy.guard import (
    CrossTenantError,
    require_tenant_context,
    require_tenant_match,
    tenants_match,
)

__all__ = [
    "CrossTenantError",
    "derive_tenant_key_material",
    "require_tenant_context",
    "require_tenant_match",
    "tenant_field_encryptor",
    "tenants_match",
]
