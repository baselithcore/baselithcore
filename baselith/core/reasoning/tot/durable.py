"""Checkpoint recording for the Tree-of-Thoughts engine.

Split from :mod:`baselith.core.reasoning.tot.engine` (module size cap). The
engine instance is shared by every request, so the scope of the ``solve`` in
progress travels in a ``ContextVar``, not on the instance.

Granularity: one step per thought-generation call (an expansion's sandbox runs
included) and one per thought evaluation, keyed by a hash of the state being
expanded or the thought being scored. Selection, backpropagation and the
early stop are deterministic functions of those results, so a resumed search
rebuilds the same tree from the record and only pays for the calls the
interrupted attempt never finished. A call that failed is not recorded.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Awaitable, Callable, Iterator
from contextvars import ContextVar
from typing import Any

__all__ = ["digest", "recorded", "search_scope"]

_tot_checkpoint: ContextVar[Any | None] = ContextVar("tot_checkpoint", default=None)


def digest(text: str) -> str:
    """Short stable hash used in step identities."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@contextlib.contextmanager
def search_scope(checkpoint: Any | None, identity: dict[str, Any]) -> Iterator[None]:
    """Bind the ``tot`` child scope of ``checkpoint`` (or the ambient one)."""
    from baselith.core.orchestration.checkpoint_scope import resolve_checkpoint

    parent = resolve_checkpoint(checkpoint)
    scope = parent.scope("tot", identity) if parent is not None else None
    token = _tot_checkpoint.set(scope)
    try:
        yield
    finally:
        _tot_checkpoint.reset(token)


async def recorded(name: str, args: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """``fn()`` as a step of the running search's scope, or a plain call."""
    from baselith.core.orchestration.checkpoint_scope import durable_step

    return await durable_step(
        _tot_checkpoint.get(), name, args, fn, category="reasoning"
    )
