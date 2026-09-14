"""Tenant-scoped tier storage for :class:`~core.memory.hierarchy.HierarchicalMemory`.

The STM / MTM / LTM containers were plain instance attributes. A
``HierarchicalMemory`` is typically long-lived and shared across requests, so
one tenant's recent context was promoted, summarized and then read back into
another tenant's prompt — a cross-tenant leak that no store-level guard sits
in front of, because nothing in the tier path resolves a resource id.

This mixin re-declares the five containers as tenant-scoped descriptors (see
:mod:`core.memory.tenant_state`). Every reader and writer keeps working
verbatim — including the slice-rebinds in
:meth:`~core.memory.hierarchy.HierarchicalMemory.consolidate_stm` and the
``memory._mtm = [...]`` / ``memory._ltm = deque(...)`` rebinds in
:mod:`core.memory.lifecycle` — while operating only on the calling tenant's
containers.

It lives in its own module so ``hierarchy.py`` stays under the 500-line cap.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from .tenant_state import TenantScopedState
from .types import MemoryItem

__all__ = ["HierarchyTiersMixin", "new_ltm_container"]


def new_ltm_container(instance: Any) -> deque[MemoryItem]:
    """Build a tenant's LTM deque, bounded by that store's ``ltm.max_items``.

    A bounded deque keeps eviction at cap O(1) instead of O(n); the bound is
    read from the owning store's config, so a per-tenant container is shaped
    exactly like the single shared one it replaces.
    """
    return deque(maxlen=instance.config.ltm.max_items)


class HierarchyTiersMixin:
    """Per-tenant STM / MTM containers behind the original attribute names."""

    _stm: TenantScopedState[list[MemoryItem]] = TenantScopedState(lambda _self: [])
    _stm_embeddings: TenantScopedState[list[list[float]]] = TenantScopedState(
        lambda _self: []
    )
    _mtm: TenantScopedState[list[MemoryItem]] = TenantScopedState(lambda _self: [])
    _mtm_embeddings: TenantScopedState[list[list[float]]] = TenantScopedState(
        lambda _self: []
    )

    # ``_ltm`` is deliberately NOT declared here: it is a ``deque``, which
    # narrows the ``Iterable[MemoryItem]`` that the search and context mixins
    # declare. Declared on this mixin that reads as two *sibling* bases
    # disagreeing; declared on ``HierarchicalMemory`` itself it reads as the
    # subclass narrowing its bases — which is what it is, and what the
    # pre-descriptor code did with an ``__init__`` annotation.
