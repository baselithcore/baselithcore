"""
Dependency Injection container and service registry.

This module provides a simple but powerful DI container for managing
service dependencies and enabling testability.
"""

from core.di.container import (
    DependencyContainer,
    Scope,
    ScopeNotActiveError,
    ServiceLifetime,
    ServiceNotFoundError,
    ServiceRegistry,
)
from core.di.lazy_registry import (
    LazyServiceRegistry,
    ResourceType,
    get_lazy_registry,
    reset_lazy_registry,
)

__all__ = [
    "DependencyContainer",
    "LazyServiceRegistry",
    "ResourceType",
    "Scope",
    "ScopeNotActiveError",
    "ServiceLifetime",
    "ServiceNotFoundError",
    "ServiceRegistry",
    "get_lazy_registry",
    "reset_lazy_registry",
]
