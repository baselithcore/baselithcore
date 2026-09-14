"""Attribute a flow dispatch to its owning plugin and to an agent span.

An intent dispatch is the framework's unit of agent work: the orchestrator
picks the handler registered for an intent and hands it the query. Two things
have to be true for the duration of that call, and until now only the first
was:

1. the **plugin context** names the handler's owning plugin, so downstream
   seams (notably the central per-plugin LLM policy) bill the work correctly;
2. an **agent span** is open, so the trace stream records which agent ran, under
   which parent, and for which plugin — the data a topology view is built from.

Both are scoped to exactly the same call, so they belong in one context manager
rather than two nested blocks at every dispatch site. Keeping the body here also
keeps :mod:`core.orchestration.mixins.execution` under the module size cap.

Domain-agnostic: it knows about intents, handlers and owners, never about what
any particular handler does.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from core.context import reset_plugin_context, set_plugin_context
from core.observability.agent_spans import agent_span
from core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = ["dispatch_attribution", "intent_owner"]


def intent_owner(orchestrator: Any, intent: str) -> str | None:
    """Return the plugin owning *intent*'s flow handler, or ``None``.

    ``None`` covers three cases that need no distinction here: the orchestrator
    has no registry, the intent is core-owned, or the lookup failed. Attribution
    is best-effort — a registry that raises must not break a dispatch.

    Args:
        orchestrator: The orchestrator performing the dispatch.
        intent: Intent being dispatched.

    Returns:
        The owning plugin's name, or ``None`` when it cannot be determined.
    """
    registry = getattr(orchestrator, "plugin_registry", None)
    if registry is None:
        return None
    try:
        owner = registry.get_flow_handler_owner(intent)
    except Exception:
        logger.debug("flow_handler_owner_lookup_failed", exc_info=True)
        return None
    return owner or None


@contextlib.contextmanager
def dispatch_attribution(orchestrator: Any, intent: str) -> Iterator[str | None]:
    """Bind the owning plugin and open the agent span for one intent dispatch.

    The agent's display name is the intent, which is what an operator recognises
    in a topology view; its id is qualified with the owning plugin so two
    plugins may register similarly named intents without collapsing into one
    node.

    Args:
        orchestrator: The orchestrator performing the dispatch.
        intent: Intent being dispatched.

    Yields:
        The owning plugin's name, or ``None`` for a core-owned intent.
    """
    owner = intent_owner(orchestrator, intent)
    token = set_plugin_context(owner) if owner else None
    try:
        with agent_span(intent, plugin=owner, kind="handler"):
            yield owner
    finally:
        if token is not None:
            reset_plugin_context(token)
