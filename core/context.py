"""
Context management for the BaselithCore framework.

This module provides thread-safe and async-safe context propagation using
Python's `contextvars`. It is primarily used to track user/tenant identity
across asynchronous call stacks without passing it explicitly through every function.
"""

from __future__ import annotations

import contextvars
from typing import Optional

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
