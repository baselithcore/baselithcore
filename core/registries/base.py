"""Generic, thread-safe registry base class.

Across the codebase (and across plugins) the same CRUD-registry shape recurs:
a name → object map with ``register`` / ``get`` / ``list`` / ``remove``. Each
copy reimplements the dict, the locking, and the lookup semantics slightly
differently. :class:`BaseRegistry` is the single domain-agnostic implementation
they can all share.

Example
-------
::

    from core.registries import BaseRegistry

    class Skill:
        def __init__(self, name: str): self.name = name

    registry: BaseRegistry[Skill] = BaseRegistry()
    registry.register(Skill("search"))          # key taken from ``.name``
    registry.register(obj, name="explicit")     # or pass an explicit name
    registry.get("search")                       # -> Skill | None
    registry.list(lambda s: s.name.startswith("s"))
"""

from __future__ import annotations

import builtins
import threading
from collections.abc import Callable, Iterator
from typing import Generic, TypeVar

from core.exceptions import DuplicateRegistrationError, ItemNotFoundError

T = TypeVar("T")


class BaseRegistry(Generic[T]):
    """A thread-safe, name-keyed registry of items of type ``T``.

    Keys are resolved per item, in order: an explicit ``name`` passed to
    :meth:`register`; else the ``key`` callable supplied at construction; else
    the item's ``name`` attribute. If none yields a string, registration raises
    ``KeyError``.
    """

    def __init__(self, *, key: Callable[[T], str] | None = None) -> None:
        """Initialize an empty registry.

        Args:
            key: Optional function deriving the registry key from an item. When
                omitted, items are keyed by their ``name`` attribute.
        """
        self._items: dict[str, T] = {}
        self._lock = threading.RLock()
        self._key = key

    def _key_for(self, item: T, name: str | None) -> str:
        if name is not None:
            return name
        if self._key is not None:
            return self._key(item)
        attr = getattr(item, "name", None)
        if isinstance(attr, str) and attr:
            return attr
        raise KeyError(
            "cannot derive registry key: pass name=... or construct the "
            "registry with key=..., or give the item a 'name' attribute"
        )

    def register(
        self, item: T, name: str | None = None, *, overwrite: bool = True
    ) -> str:
        """Register ``item`` and return the key it was stored under.

        Args:
            item: The object to register.
            name: Explicit key; if omitted it is derived (see class docstring).
            overwrite: When False, raise :class:`DuplicateRegistrationError` if
                the key already exists.
        """
        resolved = self._key_for(item, name)
        with self._lock:
            if not overwrite and resolved in self._items:
                raise DuplicateRegistrationError(f"'{resolved}' is already registered")
            self._items[resolved] = item
        return resolved

    def remove(self, name: str) -> bool:
        """Remove the item registered under ``name``.

        Returns:
            True if an item was removed, False if the name was not present.
        """
        with self._lock:
            return self._items.pop(name, None) is not None

    def get(self, name: str) -> T | None:
        """Return the item registered under ``name``, or None if absent."""
        with self._lock:
            return self._items.get(name)

    def require(self, name: str) -> T:
        """Return the item registered under ``name`` or raise.

        Raises:
            ItemNotFoundError: if ``name`` is not registered.
        """
        with self._lock:
            try:
                return self._items[name]
            except KeyError:
                raise ItemNotFoundError(f"'{name}' is not registered") from None

    def list(self, predicate: Callable[[T], bool] | None = None) -> builtins.list[T]:
        """Return all registered items, optionally filtered by ``predicate``."""
        with self._lock:
            values = list(self._items.values())
        if predicate is None:
            return values
        return [v for v in values if predicate(v)]

    def names(self) -> builtins.list[str]:
        """Return all registered keys."""
        with self._lock:
            return list(self._items.keys())

    def clear(self) -> None:
        """Remove all registered items."""
        with self._lock:
            self._items.clear()

    def __contains__(self, name: object) -> bool:
        with self._lock:
            return name in self._items

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def __iter__(self) -> Iterator[T]:
        return iter(self.list())


__all__ = ["BaseRegistry"]
