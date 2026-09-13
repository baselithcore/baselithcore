"""Request-scoped and tenant-scoped ``Colony`` ownership for swarm handlers.

The orchestrator builds one :class:`~core.orchestration.handlers.swarm_handler.SwarmHandler`
for the whole process, and the handler used to build one
:class:`~core.swarm.colony.Colony` in ``__init__``. Every request therefore
shared one agent registry, one auction and — the sharp edge — **one pheromone
field**: a failure signal deposited while serving tenant A steered tenant B's
bidding a moment later, and a dynamic agent minted for one request competed in
every later request's auctions.

This module supplies the ownership model:

* :func:`request_colony_scope` binds a freshly minted colony to the current
  request through a :class:`~contextvars.ContextVar`, so concurrent requests
  (and the tasks they spawn) each see their own;
* :class:`ColonyScopeMixin` resolves ``self._colony`` to that scoped colony
  when one is bound, and otherwise to a **tenant-keyed** colony — so even a
  caller that never opens a scope (a handler subclass with its own entry
  point) cannot leak pheromone state across tenants.

The tenant registry is a bounded LRU: colonies are cheap to rebuild (the
virtual-agent roster is re-registered on creation) and an unbounded map keyed
by tenant is a memory leak on a multi-tenant deployment.
"""

from __future__ import annotations

import contextvars
from collections import OrderedDict
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.swarm.colony import Colony

logger = get_logger(__name__)

#: Tenants whose fallback colony is retained. Beyond this the least recently
#: used one is dropped and rebuilt on next use.
MAX_TENANT_COLONIES: Final[int] = 64

#: Key used when no tenant is resolvable (scripts, background jobs).
UNSCOPED_TENANT: Final[str] = "default"

_request_colony: contextvars.ContextVar[Colony | None] = contextvars.ContextVar(
    "swarm_request_colony", default=None
)


def current_colony() -> Colony | None:
    """The colony bound to the current request, or ``None`` outside a scope."""
    return _request_colony.get()


@contextmanager
def request_colony_scope(colony: Colony) -> Generator[Colony]:
    """Bind ``colony`` as the current request's colony for the block.

    Args:
        colony: The freshly minted colony serving this request.

    Yields:
        The same colony, for convenience.
    """
    token = _request_colony.set(colony)
    try:
        yield colony
    finally:
        _request_colony.reset(token)


def current_tenant_key() -> str:
    """Tenant id for the fallback registry, never raising.

    ``get_current_tenant_id`` fails closed under ``strict_tenant_isolation``
    when no tenant is bound; a handler used from a script or a background job
    is a legitimate un-bound caller, so that failure degrades to a dedicated
    ``default`` bucket rather than propagating.
    """
    try:
        from core.context import get_current_tenant_id

        return get_current_tenant_id() or UNSCOPED_TENANT
    except Exception:  # silent-ok: an unbound tenant context is a legitimate script/background caller; it gets its own bucket, never a shared one
        return UNSCOPED_TENANT


class ColonyScopeMixin:
    """Resolves ``self._colony`` to a request- or tenant-scoped colony.

    Subclasses must call :meth:`_init_colony_scope` before touching
    ``self._colony`` and implement nothing else; the mixin owns the whole
    lifecycle.
    """

    def _init_colony_scope(
        self,
        factory: Callable[[], Colony],
        *,
        max_tenants: int = MAX_TENANT_COLONIES,
    ) -> None:
        """Wire the colony factory and the bounded per-tenant fallback map."""
        self._colony_factory = factory
        self._max_tenant_colonies = max(1, max_tenants)
        self._tenant_colonies: OrderedDict[str, Colony] = OrderedDict()
        self._colony_override: Colony | None = None

    def new_colony(self) -> Colony:
        """Mint a colony for one request (virtual agents already registered)."""
        return self._colony_factory()

    def _tenant_colony(self) -> Colony:
        """The fallback colony for the current tenant, built on first use."""
        key = current_tenant_key()
        colony = self._tenant_colonies.pop(key, None)
        if colony is None:
            colony = self.new_colony()
            logger.debug("swarm_tenant_colony_created tenant=%s", key)
        self._tenant_colonies[key] = colony
        while len(self._tenant_colonies) > self._max_tenant_colonies:
            evicted, _ = self._tenant_colonies.popitem(last=False)
            logger.debug("swarm_tenant_colony_evicted tenant=%s", evicted)
        return colony

    @property
    def _colony(self) -> Colony:
        """The colony serving the current request.

        A colony bound by :func:`request_colony_scope` wins, then an
        explicitly pinned one (see the setter), then the tenant-keyed
        fallback — so pheromone and agent state never crosses a tenant
        boundary even on entry points that open no scope.
        """
        scoped = _request_colony.get()
        if scoped is not None:
            return scoped
        if self._colony_override is not None:
            return self._colony_override
        return self._tenant_colony()

    @_colony.setter
    def _colony(self, colony: Colony) -> None:
        """Pin a colony for calls made **outside** a request scope.

        Kept because ``handler._colony = colony`` was the documented way to
        inject a colony into a handler (test harnesses do exactly this). The
        pin deliberately loses to a request scope, so injecting one can never
        put a shared colony back on the served request path.
        """
        self._colony_override = colony


__all__ = [
    "MAX_TENANT_COLONIES",
    "UNSCOPED_TENANT",
    "ColonyScopeMixin",
    "current_colony",
    "current_tenant_key",
    "request_colony_scope",
]
