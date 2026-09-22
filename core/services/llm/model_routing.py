"""Cost-aware model routing glue for the LLM service.

Resolves an optional ``task_category`` hint into a model id via
:class:`core.models.routing.ModelRouter`, driven by ``LLMConfig``:
``routing_enabled`` gates the feature and ``routing_policy`` (JSON object,
category value -> model id) overrides the built-in default policy.

Routing is a hint, never an error: unknown categories, invalid policy JSON,
a disabled router, or a pick the configured provider cannot serve all resolve
to ``None`` so the caller falls back to the config default model. Explicit
per-call models and policy-pinned models are resolved *before* routing in
``LLMService._resolve_model``.

An optional ``routing_max_cost_per_1k_usd`` config attribute (read via
``getattr`` so it works whether or not ``LLMConfig`` declares the field yet)
is forwarded as the router's ``max_cost_per_1k_usd`` budget hint: when the
category's normal pick is pricier than the hint, the router substitutes the
cheapest candidate in its policy pool that fits instead.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import TYPE_CHECKING

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.models.routing import ModelRouter

logger = get_logger(__name__)

#: Model-id prefix -> the vendor family that publishes ids of that shape. Only
#: families whose naming is actually reserved are listed: a local tag
#: (``llama3.2``, ``qwen2.5-coder``) is whatever the operator pulled, so it has
#: no recognizable family and is never second-guessed.
_MODEL_FAMILIES: tuple[tuple[str, str], ...] = (
    ("claude-", "anthropic"),
    ("gpt-", "openai"),
    ("o1-", "openai"),
    ("o3-", "openai"),
    ("gemini-", "gemini"),
)

#: Which configured providers serve each family. ``LLMConfig.provider`` itself
#: only accepts openai/ollama/huggingface/anthropic/gemini — Anthropic behind
#: Bedrock or Vertex is ``provider="anthropic"`` plus an Anthropic backend, and
#: already resolves to the Anthropic family. The reseller names are listed for
#: the duck-typed configs this module is called with elsewhere, so that such a
#: caller is not refused a model its provider genuinely serves.
_FAMILY_PROVIDERS: dict[str, frozenset[str]] = {
    "anthropic": frozenset({"anthropic", "bedrock", "vertex"}),
    "openai": frozenset({"openai", "azure", "azure_openai"}),
    "gemini": frozenset({"gemini", "vertex", "google"}),
}


def _is_servable(model_id: str, provider: object) -> bool:
    """Whether *provider* can plausibly serve *model_id*.

    The built-in policy names Claude models for every category, so enabling
    routing on an OpenAI or Ollama deployment used to ask that provider for a
    model it has never heard of — one 404 per categorized call, from a feature
    sold as a cost optimization.

    The check is deliberately one-sided: it rejects only a *provable* mismatch,
    a recognizable family served by someone else. An id whose family cannot be
    recognized is the operator's own choice and passes, as does a config that
    names no provider — duck-typed callers must keep working.

    Args:
        model_id: Model the router selected.
        provider: The configured provider name, or anything non-string when
            the config does not name one.

    Returns:
        ``True`` unless the model belongs to a family this provider does not
        serve.
    """
    if not isinstance(provider, str) or not provider:
        return True
    family = next(
        (fam for prefix, fam in _MODEL_FAMILIES if model_id.startswith(prefix)), None
    )
    if family is None:
        return True
    return provider.strip().lower() in _FAMILY_PROVIDERS[family]


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
    max_cost_per_1k_usd = getattr(config, "routing_max_cost_per_1k_usd", None)
    try:
        category = TaskCategory(task_category)
        model_id = router.select(
            category, max_cost_per_1k_usd=max_cost_per_1k_usd
        ).model_id
    except (ValueError, KeyError):
        # Unknown category or category absent from the policy.
        return None
    provider = getattr(config, "provider", None)
    if not _is_servable(model_id, provider):
        logger.warning(
            f"Routing picked {model_id!r}, which provider {provider!r} does not "
            "serve; falling back to the configured default model. Set "
            "LLM_ROUTING_POLICY to this provider's own model ids to route."
        )
        return None
    return model_id
