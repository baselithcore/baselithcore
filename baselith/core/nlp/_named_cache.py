"""One cached model per *resolved* name, however the caller spelled it.

``functools.cache`` keys on the literal arguments, so ``get_embedder()``,
``get_embedder(None)`` and ``get_embedder("<the configured model>")`` were
three cache entries for one model: three sets of weights in memory, three
load times, three private embedding caches. All three spellings are in use
across memory, indexing and chat, and the memory hierarchy's "is this the
same embedder?" identity check failed between them, so a recall embedded its
query twice. Resolving the default *before* the lookup makes them one entry.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from typing import Any


class NamedModelCache[T]:
    """A ``get_model(name=None)`` accessor memoised on the resolved name.

    Args:
        default_name: Returns the configured model name; consulted on each
            call that passes none, so a changed setting is honoured.
        build: Loads the model for a resolved name; runs once per name.
    """

    def __init__(self, default_name: Callable[[], str], build: Callable[[str], T]):
        self._default_name = default_name
        self._build = cache(build)
        self.__doc__ = build.__doc__
        self.__name__ = getattr(build, "__name__", "get_model").lstrip("_")

    def __call__(self, model_name: str | None = None) -> T:
        """Return the shared instance for ``model_name`` (config default if unset)."""
        return self._build(model_name or self._default_name())

    def cache_clear(self) -> None:
        """Drop every cached instance (tests, configuration reloads)."""
        self._build.cache_clear()

    def cache_info(self) -> Any:
        """Hit/miss statistics of the underlying cache."""
        return self._build.cache_info()


__all__ = ["NamedModelCache"]
