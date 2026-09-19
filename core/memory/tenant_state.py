"""Per-tenant in-process memory containers.

The volatile tiers of :class:`core.memory.manager.AgentMemory` (working
memory) and :class:`core.memory.hierarchy.HierarchicalMemory` (STM / MTM /
LTM) used to be plain instance lists. Both classes are routinely held as
process-wide singletons — :func:`core.memory.get_memory` returns one for the
whole process — so whatever one tenant's turn appended, the next tenant's
context builder read straight back out and handed to the model. Nothing in
that path resolves a resource id, so none of the store-level guards in
:mod:`core.tenancy` could catch it.

:class:`TenantScopedState` is a data descriptor that keeps *one container per
tenant* behind a single attribute name. Every existing reader and writer —
including the mixins in :mod:`core.memory.mixins` and
:mod:`core.memory.lifecycle`, which ``append``, ``pop``, slice and reassign
these attributes directly — keeps working unchanged while operating only on
the calling tenant's container. No public method signature changes.

Lifetime note: containers are created lazily per tenant and kept for the life
of the owning object. Each one is individually bounded (working memory by
``working_memory_limit``, the hierarchy tiers by their ``TierConfig``), so a
deployment's in-process memory scales with *active tenants* rather than with
traffic. Nothing evicts an idle tenant's container: dropping it would silently
lose that tenant's working set, which is worse than holding a bounded buffer.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.context import get_current_tenant_id

__all__ = ["TenantScopedState", "current_memory_tenant"]


#: Where the per-object container map is stashed. Deliberately *not* the name
#: of any descriptor, so a data descriptor can never shadow its own storage.
_STATE_ATTR = "_tenant_scoped_containers"


def current_memory_tenant() -> str:
    """Return the tenant that owns the in-process memory containers.

    Delegates to :func:`core.context.get_current_tenant_id`, so the behaviour
    matches the rest of the framework: ``"default"`` when nothing is bound and
    ``strict_tenant_isolation`` is off, and the existing ``TenantContextError``
    when it is on. Under strict isolation an unbound caller must not silently
    land in a shared buffer — that is precisely the pooling this module exists
    to remove.

    Returns:
        The active tenant id.

    Raises:
        core.context.TenantContextError: Strict isolation is enabled and no
            tenant is bound to the current context.
    """
    return get_current_tenant_id()


class TenantScopedState[T]:
    """Expose a per-tenant container under one ordinary attribute name.

    Assign an instance at class level in place of what used to be an instance
    attribute::

        class AgentMemory:
            _working_memory: TenantScopedState[list[MemoryItem]] = TenantScopedState(
                lambda _self: []
            )

    Reads and writes of ``self._working_memory`` then resolve to the container
    belonging to the tenant bound to the current context.

    Args:
        factory: Builds a fresh, empty container for a tenant. It receives the
            owning instance, so a container whose shape depends on
            configuration (e.g. ``deque(maxlen=config.ltm.max_items)``) can be
            constructed correctly.
    """

    __slots__ = ("_factory", "_name")

    def __init__(self, factory: Callable[[Any], T]) -> None:
        self._factory = factory
        self._name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        """Record the attribute name this descriptor was bound to."""
        self._name = name

    @staticmethod
    def _containers(instance: Any) -> dict[tuple[str, str], Any]:
        # ``instance.__dict__`` is ``Any``; pin the annotation here so the
        # declared return type is honoured instead of laundering ``Any`` out.
        containers: dict[tuple[str, str], Any] | None = instance.__dict__.get(
            _STATE_ATTR
        )
        if containers is None:
            containers = {}
            instance.__dict__[_STATE_ATTR] = containers
        return containers

    def __get__(self, instance: Any, owner: type | None = None) -> T:
        """Return the calling tenant's container, creating it on first use."""
        if instance is None:  # accessed on the class, e.g. by help()/inspect
            return self  # type: ignore[return-value]
        containers = self._containers(instance)
        key = (current_memory_tenant(), self._name)
        container = containers.get(key)
        if container is None:
            # Check-then-create without a lock, deliberately. Two coroutines in
            # the same event loop cannot interleave here: there is no await
            # between the check and the store, and CPython holds the GIL across
            # these bytecodes. Two *threads* could both miss and build a
            # container, in which case the loser's is discarded by the dict
            # assignment — each thread would then hold a different list for the
            # same tenant until the next read. That is the same exposure the
            # plain instance lists had (the tiers were never thread-safe: the
            # mixins append and pop them unlocked), so a lock here would buy
            # nothing while adding one acquisition to every tier access on the
            # request path. The tiers are async-owned; keep them that way.
            container = self._factory(instance)
            containers[key] = container
        return container  # type: ignore[no-any-return]

    def __set__(self, instance: Any, value: T) -> None:
        """Replace the calling tenant's container (e.g. after a slice-rebind)."""
        self._containers(instance)[(current_memory_tenant(), self._name)] = value
