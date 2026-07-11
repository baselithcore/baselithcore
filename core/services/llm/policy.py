"""Central per-plugin LLM policy seam.

An operator may pin, per plugin, which LLM provider (and model) the shared LLM
funnel should use — e.g. route a coding plugin to Anthropic while the rest of
the deployment stays on the config default. The policy *source* is
domain-specific (it lives in an admin plugin's store), so core only exposes a
registration seam and never imports the plugin — the same Sacred-Core pattern
as ``core.context.set_plugin_tenancy_resolver``.

Resolution is identity-derived and safe by construction:

* The *calling plugin* comes from :func:`core.context.get_current_plugin`,
  bound only at framework chokepoints (plugin-context middleware, orchestrator
  dispatch) — never self-declared by the plugin.
* The resolver must be cheap and total (cached, never raising); any failure or
  invalid value degrades to "no policy", i.e. the deployment-default provider.
* Credentials are **not** part of a policy. A policy names a provider/model;
  the provider's key always comes from central configuration
  (:class:`core.config.services.LLMConfig`), so secrets never live in the
  policy store.

When no resolver is registered behaviour is identical to a deployment without
policies: every caller gets the config-default service.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.observability.logging import get_logger

logger = get_logger(__name__)

#: Providers the shared LLM funnel can construct (mirrors ``LLMConfig.provider``).
SUPPORTED_PROVIDERS: tuple[str, ...] = ("openai", "ollama", "huggingface", "anthropic")


@dataclass(frozen=True)
class PluginLLMPolicy:
    """An operator-pinned LLM routing decision for one plugin.

    Attributes:
        provider: Provider to route the plugin's LLM calls to, or ``None`` to
            keep the deployment default. Must be one of
            :data:`SUPPORTED_PROVIDERS` (invalid values are dropped at
            resolution time).
        model: Model id to pin. When set it wins over the plugin's own
            per-call ``model=`` overrides — a pin is governance, not a hint.
            ``None`` keeps the provider's configured default model.
    """

    provider: str | None = None
    model: str | None = None

    def is_empty(self) -> bool:
        """True when the policy pins nothing (equivalent to no policy)."""
        return not (self.provider or self.model)


# A resolver is called ``resolver(plugin_name, scope)`` where ``scope`` is an
# optional named sub-policy (see ``PluginMetadata.llm_scopes``) or ``None`` for
# the plugin default. Resolvers registered with the older single-argument
# signature are still supported (see ``_call_resolver``).
_PluginLLMPolicyResolver = Callable[[str, "str | None"], "PluginLLMPolicy | None"]
_plugin_llm_policy_resolver: _PluginLLMPolicyResolver | None = None


def set_plugin_llm_policy_resolver(
    resolver: _PluginLLMPolicyResolver | None,
) -> None:
    """Register (or clear, with ``None``) the per-plugin LLM policy source.

    An admin plugin installs this at activation. ``resolver(plugin_name,
    scope)`` returns the :class:`PluginLLMPolicy` pinned for that plugin (and
    named sub-scope, or the default when ``scope`` is ``None``), or ``None`` to
    use the deployment default. It must be cheap and total (cached, never
    raising) — it is consulted on every LLM service resolution. A resolver with
    the legacy single-argument signature ``resolver(plugin_name)`` is still
    accepted (it is called without the scope and so pins every scope alike).
    """
    global _plugin_llm_policy_resolver
    _plugin_llm_policy_resolver = resolver


def _call_resolver(
    resolver: _PluginLLMPolicyResolver, plugin_name: str, scope: str | None
) -> PluginLLMPolicy | None:
    """Invoke the resolver, tolerating the legacy single-arg signature.

    Never raises: any resolver error degrades to ``None`` (deployment default).
    """
    try:
        return resolver(plugin_name, scope)
    except TypeError:
        # Back-compat: a resolver registered with the old ``(plugin_name,)``
        # signature — call it without the scope (it pins every scope alike).
        try:
            return resolver(plugin_name)  # type: ignore[call-arg]
        except Exception:
            return None
    except Exception:
        return None


def _validate(
    policy: PluginLLMPolicy | None, plugin_name: str
) -> PluginLLMPolicy | None:
    """Drop empty policies and those naming an unsupported provider."""
    if policy is None or policy.is_empty():
        return None
    if policy.provider is not None and policy.provider not in SUPPORTED_PROVIDERS:
        logger.warning(
            "Ignoring LLM policy for plugin %r: unsupported provider %r",
            plugin_name,
            policy.provider,
        )
        return None
    return policy


def resolve_plugin_llm_policy(
    plugin_name: str, scope: str | None = None
) -> PluginLLMPolicy | None:
    """The LLM policy pinned for *plugin_name* (and *scope*), or ``None``.

    When *scope* names a declared LLM sub-policy (e.g. ``"ingestion"``) that has
    no pin of its own, resolution **falls back to the plugin's default pin**
    (``scope=None``); only then to the deployment default. Degrades to ``None``
    whenever no resolver is registered, the resolver raises, or the policy is
    empty/invalid — a policy-store outage or a bad row can never break LLM
    availability. A policy naming an unsupported provider is dropped entirely
    (its ``model`` alone could belong to that provider).
    """
    resolver = _plugin_llm_policy_resolver
    if resolver is None:
        return None
    policy = _validate(_call_resolver(resolver, plugin_name, scope), plugin_name)
    if policy is None and scope is not None:
        policy = _validate(_call_resolver(resolver, plugin_name, None), plugin_name)
    return policy


def resolve_active_llm_policy() -> PluginLLMPolicy | None:
    """The LLM policy for the plugin bound to the current execution context.

    Combines :func:`core.context.get_current_plugin` (who is calling) with
    :func:`resolve_plugin_llm_policy` (what is pinned for them). Returns
    ``None`` — i.e. use the deployment default — when no plugin is bound.
    """
    from core.context import get_current_plugin

    plugin = get_current_plugin()
    if not plugin:
        return None
    return resolve_plugin_llm_policy(plugin)


__all__ = [
    "SUPPORTED_PROVIDERS",
    "PluginLLMPolicy",
    "resolve_active_llm_policy",
    "resolve_plugin_llm_policy",
    "set_plugin_llm_policy_resolver",
]
