"""
High-Performance Dependency Injection (DI) Engine.

Provides a robust framework for service registration, resolution, and
lifecycle management. Supports decoupled architecture by managing
Singleton, Transient, and Scoped service lifetimes, essential for
maintaining the 'Sacred Core' agnostic logic.
"""

from core.observability.logging import get_logger
import threading
import contextvars
from enum import Enum
from typing import Any, Callable, Dict, Optional, Type, TypeVar

logger = get_logger(__name__)

T = TypeVar("T")

# Context variable for scope tracking
_current_scope: contextvars.ContextVar[Optional["Scope"]] = contextvars.ContextVar(
    "_current_scope", default=None
)


class ServiceLifetime(Enum):
    """Service lifetime options."""

    SINGLETON = "singleton"  # Single instance shared across all requests
    TRANSIENT = "transient"  # New instance created for each request
    SCOPED = "scoped"  # Single instance per scope (e.g., per HTTP request)


class ServiceNotFoundError(Exception):
    """Raised when a requested service is not registered."""

    pass


class ScopeNotActiveError(Exception):
    """Raised when trying to resolve a scoped service outside of a scope."""

    pass


class Scope:
    """
    Represents a dependency injection scope.

    Scoped services are instantiated once per scope and shared within it.
    Typically used for per-request services in web applications.

    Usage:
        container = DependencyContainer()
        container.register(DbSession, create_session, ServiceLifetime.SCOPED)

        async with container.create_scope() as scope:
            session = scope.resolve(DbSession)
            # Same instance within this scope
    """

    def __init__(self, container: "DependencyContainer") -> None:
        self._container = container
        self._instances: Dict[Type, Any] = {}
        self._lock = threading.Lock()

    def resolve(self, interface: Type[T]) -> T:
        """Resolve a service within this scope."""
        return self._container._resolve_in_scope(interface, self)

    def get_or_create(self, interface: Type[T], factory: Callable[[], T]) -> T:
        """Get cached instance or create new one for this scope."""
        with self._lock:
            if interface not in self._instances:
                self._instances[interface] = factory()
                logger.debug(f"Created scoped instance: {interface.__name__}")
            return self._instances[interface]

    async def __aenter__(self) -> "Scope":
        _current_scope.set(self)
        return self

    async def __aexit__(self, *args) -> None:
        _current_scope.set(None)
        self._instances.clear()

    def __enter__(self) -> "Scope":
        _current_scope.set(self)
        return self

    def __exit__(self, *args) -> None:
        _current_scope.set(None)
        self._instances.clear()


class ServiceRegistry:
    """
    Simple service registry for dependency injection.

    Provides a global registry for services that can be accessed
    throughout the application.
    """

    _services: Dict[Type, Any] = {}
    _lock = threading.Lock()

    @classmethod
    def register(cls, interface: Type[T], implementation: Any) -> None:
        """
        Register a service implementation for an interface.

        Args:
            interface: The interface/protocol type
            implementation: The concrete implementation instance or class
        """
        with cls._lock:
            cls._services[interface] = implementation
            logger.debug(f"Registered service: {interface.__name__}")

    @classmethod
    def get(cls, interface: Type[T]) -> T:
        """
        Get a service implementation by interface.

        Args:
            interface: The interface/protocol type

        Returns:
            The registered implementation

        Raises:
            ServiceNotFoundError: If the service is not registered
        """
        with cls._lock:
            if interface not in cls._services:
                raise ServiceNotFoundError(
                    f"Service not found for interface: {interface.__name__}"
                )
            return cls._services[interface]

    @classmethod
    def has(cls, interface: Type) -> bool:
        """
        Check if a service is registered.

        Args:
            interface: The interface/protocol type

        Returns:
            True if the service is registered, False otherwise
        """
        with cls._lock:
            return interface in cls._services

    @classmethod
    def clear(cls) -> None:
        """Clear all registered services. Useful for testing."""
        with cls._lock:
            cls._services.clear()
            logger.debug("Cleared all registered services")


class DependencyContainer:
    """
    Centralized Inversion of Control (IoC) Container.

    Manages the instantiation and sharing of core framework services.
    Implements a thread-safe registry with support for complex dependency
    graphs and context-aware scoping (e.g., per-request database sessions
    or tenant-specific configurations).
    """

    def __init__(self):
        self._services: Dict[Type, tuple[Callable, ServiceLifetime]] = {}
        self._singletons: Dict[Type, Any] = {}
        self._lock = threading.Lock()

    def register(
        self,
        interface: Type[T],
        factory: Callable[[], T],
        lifetime: ServiceLifetime = ServiceLifetime.SINGLETON,
    ) -> None:
        """
        Register a service with a factory function.

        Args:
            interface: The interface/protocol type
            factory: Factory function that creates the service instance
            lifetime: Service lifetime (SINGLETON, TRANSIENT, or SCOPED)
        """
        with self._lock:
            self._services[interface] = (factory, lifetime)
            logger.debug(
                f"Registered service: {interface.__name__} with lifetime {lifetime.value}"
            )

    def register_instance(self, interface: Type[T], instance: T) -> None:
        """
        Register a service instance directly (always singleton).

        Args:
            interface: The interface/protocol type
            instance: The service instance
        """
        with self._lock:
            self._services[interface] = (lambda: instance, ServiceLifetime.SINGLETON)
            self._singletons[interface] = instance
            logger.debug(f"Registered service instance: {interface.__name__}")

    def create_scope(self) -> Scope:
        """
        Create a new dependency injection scope.

        Returns:
            A new Scope that can be used as a context manager
        """
        return Scope(self)

    def resolve(self, interface: Type[T]) -> T:
        """
        Resolve a service by its interface.

        For scoped services, uses the current scope from context.

        Args:
            interface: The interface/protocol type

        Returns:
            The service instance

        Raises:
            ServiceNotFoundError: If the service is not registered
            ScopeNotActiveError: If resolving scoped service without active scope
        """
        scope = _current_scope.get()
        if scope is not None:
            return self._resolve_in_scope(interface, scope)
        return self._resolve_without_scope(interface)

    def _resolve_without_scope(self, interface: Type[T]) -> T:
        """Resolve service without an active scope."""
        with self._lock:
            if interface not in self._services:
                raise ServiceNotFoundError(
                    f"Service not found for interface: {interface.__name__}"
                )

            factory, lifetime = self._services[interface]

            if lifetime == ServiceLifetime.SCOPED:
                raise ScopeNotActiveError(
                    f"Cannot resolve scoped service {interface.__name__} without active scope. "
                    "Use 'async with container.create_scope() as scope:' first."
                )

            if lifetime == ServiceLifetime.SINGLETON:
                if interface not in self._singletons:
                    self._singletons[interface] = factory()
                    logger.debug(f"Created singleton instance: {interface.__name__}")
                return self._singletons[interface]
            else:
                instance = factory()
                logger.debug(f"Created transient instance: {interface.__name__}")
                return instance

    def _resolve_in_scope(self, interface: Type[T], scope: Scope) -> T:
        """Resolve service within a specific scope."""
        with self._lock:
            if interface not in self._services:
                raise ServiceNotFoundError(
                    f"Service not found for interface: {interface.__name__}"
                )

            factory, lifetime = self._services[interface]

            if lifetime == ServiceLifetime.SINGLETON:
                if interface not in self._singletons:
                    self._singletons[interface] = factory()
                    logger.debug(f"Created singleton instance: {interface.__name__}")
                return self._singletons[interface]
            elif lifetime == ServiceLifetime.SCOPED:
                return scope.get_or_create(interface, factory)
            else:
                instance = factory()
                logger.debug(f"Created transient instance: {interface.__name__}")
                return instance

    def has(self, interface: Type) -> bool:
        """
        Check if a service is registered.

        Args:
            interface: The interface/protocol type

        Returns:
            True if the service is registered, False otherwise
        """
        with self._lock:
            return interface in self._services

    def clear(self) -> None:
        """Clear all registered services and singletons. Useful for testing."""
        with self._lock:
            self._services.clear()
            self._singletons.clear()
            logger.debug("Cleared dependency container")
