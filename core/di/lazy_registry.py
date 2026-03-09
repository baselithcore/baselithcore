"""
Lazy Service Registry for on-demand resource initialization.

This module provides a mechanism to register factory functions for core
resources (LLM, VectorStore, Databases, etc.) that are only executed when
a service is actually needed by a plugin or handler.

This helps in:
- Fast startup: Avoiding heavy connections at boot time.
- Resource economy: Only initializing what is actually used.
- Thread-safe concurrency: Ensuring singletons are created correctly across
  async tasks.
"""

import asyncio
from core.observability.logging import get_logger
from typing import Any, Callable, Dict, Optional, Type, TypeVar, Awaitable, Union
from enum import Enum
import threading

logger = get_logger(__name__)

T = TypeVar("T")


class ResourceType(str, Enum):
    """
    Standard core resource identifiers that components can depend on.
    """

    LLM = "llm"
    VECTORSTORE = "vectorstore"
    GRAPH = "graph"
    POSTGRES = "postgres"
    REDIS = "redis"
    MEMORY = "memory"
    EVALUATION = "evaluation"
    EVOLUTION = "evolution"


class LazyServiceRegistry:
    """
    Asynchronous registry for lazy-initialized singleton services.

    Manages a set of factory functions and ensures that each service is
    initialized at most once in a thread-safe and async-safe manner.
    """

    def __init__(self):
        """Initialize the lazy registry state."""
        self._factories: Dict[Union[Type, str], Callable[[], Awaitable[Any]]] = {}
        self._instances: Dict[Union[Type, str], Any] = {}
        self._locks: Dict[Union[Type, str], asyncio.Lock] = {}
        self._initialized: Dict[Union[Type, str], bool] = {}
        self._thread_lock = threading.Lock()

    def _get_name(self, interface: Any) -> str:
        """
        Robustly retrieve a string name for an interface type or key.

        Converts types to their `__name__` and handles string keys
        directly for consistent logging and lookups.

        Args:
            interface: The interface type or unique string key.

        Returns:
            str: Human-readable name or key.
        """
        if isinstance(interface, str):
            return interface
        return getattr(interface, "__name__", str(interface))

    def register_factory(
        self,
        interface: Union[Type[T], str],
        factory: Callable[[], Awaitable[T]],
    ) -> None:
        """
        Register an asynchronous factory function for a service.

        Args:
            interface: The interface type or unique string key for the service.
            factory: An async callable that returns the service instance.
        """
        with self._thread_lock:
            self._factories[interface] = factory
            self._locks[interface] = asyncio.Lock()
            self._initialized[interface] = False
            logger.debug(f"Registered lazy factory for: {self._get_name(interface)}")

    async def get_or_create(self, interface: Union[Type[T], str]) -> T:
        """
        Retrieve a service instance, triggering lazy initialization if required.

        Ensures thread-safe and async-safe singleton creation using
        double-check locking with an `asyncio.Lock`. The provided factory
        is only executed once.

        Args:
            interface: The interface type or unique string key registered
                       via `register_factory`.

        Returns:
            T: The fully initialized singleton service instance.

        Raises:
            KeyError: If no factory has been registered for the requested
                     interface.
        """
        if interface not in self._factories:
            raise KeyError(f"No factory registered for {self._get_name(interface)}")

        # Fast path: already initialized
        if self._initialized.get(interface, False):
            return self._instances[interface]

        # Slow path: Lazy initialization with async lock
        async with self._locks[interface]:
            # Double-check after acquiring lock to handle races
            if not self._initialized.get(interface, False):
                logger.info(
                    f"🔧 Lazy initializing service: {self._get_name(interface)}"
                )
                factory = self._factories[interface]
                self._instances[interface] = await factory()
                self._initialized[interface] = True
                logger.info(f"✅ Service initialized: {self._get_name(interface)}")

            return self._instances[interface]

    def is_initialized(self, interface: Union[Type, str]) -> bool:
        """Check if a service has already been instantiated."""
        return self._initialized.get(interface, False)

    def get_initialized_services(self) -> Dict[str, bool]:
        """
        Retrieve a summary of all registered services and their status.
        """
        with self._thread_lock:
            return {
                self._get_name(interface): self._initialized.get(interface, False)
                for interface in self._factories.keys()
            }

    async def shutdown_all(self) -> None:
        """
        Shut down and clean up all initialized services.

        Attempts to call `shutdown()` or `close()` on each instance
        if the methods exist.
        """
        logger.info("🔻 Shutting down lazy-initialized services...")
        for interface, instance in list(self._instances.items()):
            if self._initialized.get(interface, False):
                try:
                    if hasattr(instance, "shutdown"):
                        await instance.shutdown()
                        logger.debug(f"Shutdown service: {self._get_name(interface)}")
                    elif hasattr(instance, "close"):
                        if asyncio.iscoroutinefunction(instance.close):
                            await instance.close()
                        else:
                            instance.close()
                        logger.debug(f"Closed service: {self._get_name(interface)}")
                except Exception as e:
                    logger.error(
                        f"Error shutting down {self._get_name(interface)}: {e}"
                    )

        self._instances.clear()
        self._initialized.clear()
        logger.info("✅ All lazy services shutdown")

    def clear(self) -> None:
        """Clear all service definitions and instances. Useful for testing."""
        with self._thread_lock:
            self._factories.clear()
            self._instances.clear()
            self._locks.clear()
            self._initialized.clear()
            logger.debug("Lazy service registry cleared")


# Global singleton instance of the lazy registry.
_lazy_registry: Optional[LazyServiceRegistry] = None
_registry_lock = threading.Lock()


def get_lazy_registry() -> LazyServiceRegistry:
    """
    Retrieve the global singleton LazyServiceRegistry instance.
    """
    global _lazy_registry
    if _lazy_registry is None:
        with _registry_lock:
            if _lazy_registry is None:
                _lazy_registry = LazyServiceRegistry()
    return _lazy_registry


def reset_lazy_registry() -> None:
    """
    Reset the global registry instance.
    Mainly used to ensure isolation between unit tests.
    """
    global _lazy_registry
    with _registry_lock:
        if _lazy_registry:
            _lazy_registry.clear()
        _lazy_registry = None
