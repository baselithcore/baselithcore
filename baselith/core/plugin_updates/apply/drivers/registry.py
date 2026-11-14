"""Named apply drivers: who registers which implementation, looked up by name.

The updater registers its drivers when it builds them; the router resolves a
name per run. An unknown name raises :class:`UnknownDriver` (a ``ValueError``,
never a ``KeyError``) so a caller cannot mistake it for a missing dict entry.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from .base import ApplyDriver

DriverFactory = Callable[[], ApplyDriver]


class UnknownDriver(ValueError):
    """No driver is registered under the name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"no apply driver named {name!r}")
        self.name = name


class DriverRegistry:
    """Driver factories by name; each is built once, on first use."""

    def __init__(self) -> None:
        self._factories: dict[str, DriverFactory] = {}
        self._built: dict[str, ApplyDriver] = {}
        self._lock = threading.Lock()

    def register(self, name: str, factory: DriverFactory) -> None:
        """Register (or replace) the factory of ``name``."""
        with self._lock:
            self._factories[name] = factory
            self._built.pop(name, None)

    def get(self, name: str) -> ApplyDriver:
        """The driver of ``name``, built on first use.

        Raises:
            UnknownDriver: Nothing is registered under ``name``.
        """
        with self._lock:
            built = self._built.get(name)
            if built is not None:
                return built
            factory = self._factories.get(name)
            if factory is None:
                raise UnknownDriver(name)
            driver = self._built[name] = factory()
            return driver

    def names(self) -> tuple[str, ...]:
        """The registered names, sorted."""
        with self._lock:
            return tuple(sorted(self._factories))


_DEFAULT = DriverRegistry()


def register_driver(name: str, factory: DriverFactory) -> None:
    """Register (or replace) ``name`` in the process-wide registry."""
    _DEFAULT.register(name, factory)


def get_driver(name: str) -> ApplyDriver:
    """The process-wide driver of ``name``; :class:`UnknownDriver` if absent."""
    return _DEFAULT.get(name)


def registered_drivers() -> tuple[str, ...]:
    """The names registered in the process-wide registry."""
    return _DEFAULT.names()


__all__ = [
    "DriverFactory",
    "DriverRegistry",
    "UnknownDriver",
    "get_driver",
    "register_driver",
    "registered_drivers",
]
