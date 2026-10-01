"""Tenant/plugin collection naming for the shared vector store.

A scoped collection is ``<tenant>.<plugin>.<name>``. None of the three parts
may contain ``.``, so the mapping is injective: no (tenant, plugin, name)
triple can produce another triple's collection. A tenant key outside the safe
alphabet (an email, a ``user:`` prefix...) is replaced by ``~h<digest>``; ``~``
is not in the safe alphabet, so a hashed key never collides with a literal one.
"""

from __future__ import annotations

import hashlib
import re

from core.services.inference.errors import TenantScopeError

SEPARATOR = "."
_SAFE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_NAME = re.compile(r"[A-Za-z0-9_-]{1,128}")


def tenant_component(tenant: str) -> str:
    """Return the collection-safe form of ``tenant`` (fail closed if empty)."""
    if not isinstance(tenant, str) or not tenant.strip():
        raise TenantScopeError("vector access requires a non-empty tenant key")
    if _SAFE.fullmatch(tenant):
        return tenant
    return "~h" + hashlib.sha256(tenant.encode()).hexdigest()[:24]


def plugin_component(plugin: str) -> str:
    """Validate a plugin name (``[A-Za-z0-9_-]``) for use in a collection."""
    if not isinstance(plugin, str) or not _SAFE.fullmatch(plugin):
        raise TenantScopeError(f"invalid plugin name for vector scope: {plugin!r}")
    return plugin


def scoped_name(tenant: str, plugin: str, name: str) -> str:
    """Build the physical collection name for a logical ``name``."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise TenantScopeError(f"invalid collection name: {name!r}")
    return SEPARATOR.join((tenant_component(tenant), plugin_component(plugin), name))


def scope_prefix(tenant: str, plugin: str) -> str:
    """Prefix shared by every collection of this tenant+plugin."""
    return SEPARATOR.join((tenant_component(tenant), plugin_component(plugin), ""))
