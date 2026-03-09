"""
Dependency Injection container and service registry.

This module provides a simple but powerful DI container for managing
service dependencies and enabling testability.
"""

from core.di.container import (
    ServiceRegistry,
    DependencyContainer,
    ServiceLifetime,
    ServiceNotFoundError,
    Scope,
    ScopeNotActiveError,
)
from core.di.lazy_registry import (
    LazyServiceRegistry,
    ResourceType,
    get_lazy_registry,
    reset_lazy_registry,
)

__all__ = [
    "ServiceRegistry",
    "DependencyContainer",
    "ServiceLifetime",
    "ServiceNotFoundError",
    "Scope",
    "ScopeNotActiveError",
    "LazyServiceRegistry",
    "ResourceType",
    "get_lazy_registry",
    "reset_lazy_registry",
]
