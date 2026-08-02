"""Cost-aware model routing glue for the LLM service.

Resolves an optional ``task_category`` hint into a model id via
:class:`core.models.routing.ModelRouter`, driven by ``LLMConfig``:
``routing_enabled`` gates the feature and ``routing_policy`` (JSON object,
category value -> model id) overrides the built-in default policy.

Routing is a hint, never an error: unknown categories, invalid policy JSON,
or a disabled router all resolve to ``None`` so the caller falls back to the
config default model. Explicit per-call models and policy-pinned models are
resolved *before* routing in ``LLMService._resolve_model``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import TYPE_CHECKING

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.models.routing import ModelRouter

logger = get_logger(__name__)


@lru_cache(maxsize=8)
def _router_for(policy_json: str) -> ModelRouter | None:
    """Build (and cache) the router for a policy JSON string."""
    from core.models.routing import ModelRouter, RoutingPolicy, TaskCategory

    if not policy_json:
        return ModelRouter()
    try:
        raw = json.loads(policy_json)
        primary = {TaskCategory(cat): model for cat, model in raw.items()}
    except (ValueError, KeyError, AttributeError) as exc:
        logger.error(f"Invalid LLM_ROUTING_POLICY, routing disabled: {exc}")
        return None
    # Unlisted categories fall back to the config default model.
    return ModelRouter(RoutingPolicy(primary=primary, complexity_upgrade={}))


def routed_model(config: object, task_category: str | None) -> str | None:
    """Model chosen by the router for *task_category*, or ``None``.

    The ``is True`` guard keeps Mock/SimpleNamespace test configs (whose
    attributes are truthy objects) from accidentally enabling routing.
    """
    if getattr(config, "routing_enabled", False) is not True or not task_category:
        return None
    from core.models.routing import TaskCategory

    policy_json = getattr(config, "routing_policy", "") or ""
    router = _router_for(policy_json)
    if router is None:
        return None
    try:
        category = TaskCategory(task_category)
        return router.select(category).model_id
    except (ValueError, KeyError):
        # Unknown category or category absent from the policy.
        return None
