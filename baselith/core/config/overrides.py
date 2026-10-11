"""Scoped configuration overrides: inject settings without patching module paths.

Every ``get_*_config()`` accessor caches one settings object per process, read
from the environment on first use. That is right for a deployment and wrong
for everything that needs a *different* configuration for a while: a test, a
script that builds two clients side by side, an embedding application that
owns its settings. Until now those callers monkeypatched the accessor where it
was imported (``patch("baselith.core.x.get_llm_config")``), which breaks
silently the day the import moves, or reset private module globals.

:func:`override_config` replaces that with injection. Inside the ``with``
block every accessor decorated with :func:`overridable` returns the instance
passed for its class instead of the cached one::

    from baselith.core.config import LLMConfig, override_config

    with override_config(LLMConfig(provider="ollama", model="llama3")):
        service = LLMService()  # reads get_llm_config() -> the override

The scope is a :class:`~contextvars.ContextVar`, so it follows the code that
runs inside the block: nested blocks stack (the inner one wins for the classes
it names), asyncio tasks created inside the block inherit it, and concurrent
requests or tests never see each other's overrides. Threads started with
:func:`asyncio.to_thread` copy the context; a bare ``ThreadPoolExecutor`` does
not. Nothing outside the block changes, and the process-wide cached instance
is never touched.

An override is keyed by its own class and by every settings class it derives
from, so a subclass instance also answers for its base.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import Any, TypeVar, cast

from pydantic import BaseModel
from pydantic_settings import BaseSettings

__all__ = ["active_override", "overridable", "override_config"]

C = TypeVar("C")

_EMPTY: Mapping[type, object] = MappingProxyType({})

_overrides: ContextVar[Mapping[type, object]] = ContextVar(
    "baselith_config_overrides", default=_EMPTY
)

#: Classes an override never answers for: they are bases of every settings
#: class, so keying on them would make one override shadow all accessors.
_NEVER_KEYED: frozenset[type] = frozenset({object, BaseModel, BaseSettings})


def _keys_for(config: object) -> list[type]:
    """Every settings class ``config`` stands in for: its own and its bases."""
    return [
        cls
        for cls in type(config).__mro__
        if cls not in _NEVER_KEYED and issubclass(cls, BaseModel)
    ]


@contextmanager
def override_config(*configs: object) -> Iterator[None]:
    """Make the given settings objects the ones their accessors return.

    Args:
        *configs: Settings instances (``LLMConfig(...)``, ``StorageConfig(...)``,
            ...). At most one per class.

    Raises:
        TypeError: If an argument is not a pydantic settings or model object.
        ValueError: If two arguments are of the same class.
    """
    added: dict[type, object] = {}
    seen: set[type] = set()
    for config in configs:
        if not isinstance(config, BaseModel):
            raise TypeError(
                f"override_config() takes settings objects, got {type(config).__name__}"
            )
        if type(config) in seen:
            raise ValueError(
                f"override_config() got two {type(config).__name__} instances"
            )
        seen.add(type(config))
        for key in _keys_for(config):
            # The most derived instance wins for a shared base, whatever the
            # argument order: an exact-class key is never displaced.
            current = added.get(key)
            if current is None or type(current) is not key:
                added[key] = config
    token = _overrides.set(MappingProxyType({**_overrides.get(), **added}))
    try:
        yield
    finally:
        _overrides.reset(token)


def active_override(cls: type[C]) -> C | None:
    """The settings object overriding ``cls`` in the current context, if any."""
    return cast("C | None", _overrides.get().get(cls))


def overridable(cls: type[C]) -> Callable[[Callable[[], C]], Callable[[], C]]:
    """Make a ``get_*_config()`` accessor honour :func:`override_config`.

    Apply it outermost, above ``functools.lru_cache`` where one is used: the
    override is consulted before the cache, and ``cache_clear`` stays reachable
    on the decorated accessor.

    Args:
        cls: The settings class the accessor returns.
    """

    def decorate(getter: Callable[[], C]) -> Callable[[], C]:
        @functools.wraps(getter)
        def accessor() -> C:
            override = _overrides.get().get(cls)
            if override is not None:
                return cast(C, override)
            return getter()

        cache_clear: Any = getattr(getter, "cache_clear", None)
        if cache_clear is not None:
            accessor.__dict__["cache_clear"] = cache_clear
        accessor.__dict__["__baselith_overridable__"] = cls
        return accessor

    return decorate
