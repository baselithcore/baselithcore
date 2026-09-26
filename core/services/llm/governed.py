"""Governed provider/model/credentials for plugins holding their own SDK.

Most framework consumers call :func:`core.services.llm.get_llm_service` and the
central per-plugin LLM policy (:mod:`core.services.llm.policy`) is applied for
them transparently. Some plugins, however, keep their own provider SDK client
(OpenAI-compatible / native Ollama) because they depend on features the shared
funnel does not model — schema-constrained decoding, Ollama ``think`` /
reasoning effort, per-role model splits, in-process embeddings. Routing those
through ``get_llm_service`` would drop the feature, so instead they resolve the
*effective* provider/model/credentials for their plugin here and point their
own client at that governed target.

This exposes exactly the routing decision the funnel already obeys
(:func:`resolve_plugin_llm_policy` × :class:`core.config.services.LLMConfig`) as
plain configuration, so a plugin bundling its own SDK can still honour an
operator's per-plugin LLM pin. Credentials come only from central
configuration — a policy names a provider/model, never a secret.

Fail-open by construction. :func:`resolve_governed_client_config` returns
``None`` — meaning "keep your own configured defaults, i.e. behaviour identical
to an unpinned deployment" — whenever the plugin has no policy pinned, the pin
is a cross-provider switch without a model (meaningless default model), or
resolution raises. Central governance must never break a plugin's LLM
availability. The consuming plugin is responsible for one more check: if
:attr:`GovernedClientConfig.provider` is one its bundled SDK cannot reach
(e.g. an OpenAI/Ollama-only sub-app pinned to ``anthropic``), it should ignore
the governed config and fall back to its own default rather than fail.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import SecretStr

from core.config import get_llm_config
from core.observability.logging import get_logger
from core.services.llm.policy import resolve_plugin_llm_policy
from core.services.llm.runtime import api_base_for, api_key_for

logger = get_logger(__name__)

#: Providers reachable through an OpenAI-compatible client: OpenAI itself and
#: a self-hosted vLLM server (see :attr:`GovernedClientConfig.speaks_openai`).
OPENAI_WIRE_PROVIDERS: frozenset[str] = frozenset({"openai", "vllm"})


@dataclass(frozen=True)
class GovernedClientConfig:
    """The effective LLM routing a plugin should point its own SDK client at.

    Attributes:
        provider: Provider the plugin's calls should route to — one of
            :data:`core.services.llm.policy.SUPPORTED_PROVIDERS`. A plugin whose
            bundled SDK cannot serve this provider must fall back to its own
            default (see the module docstring).
        model: Model id to use as the plugin's *default* model. When the policy
            pinned a model this wins over the plugin's per-call defaults (a pin
            is governance, not a hint); ``None`` means keep the provider's own
            default model. Explicit per-role model overrides inside the plugin
            are left untouched — governance sets the default, not every call.
        api_key: Central credential for ``provider`` (``None`` for keyless local
            providers such as Ollama). Wrapped in ``SecretStr``; unwrap with
            :meth:`key` only at the SDK boundary.
        api_base: The endpoint configured **for this provider**
            (:func:`core.services.llm.runtime.api_base_for`), or ``None`` to use
            the SDK default. The plugin adapts it to its own SDK's convention
            (e.g. an OpenAI-compatible client talking to Ollama appends
            ``/v1``). It is never the default provider's URL carried across a
            provider switch — that would aim the client at the wrong server.
    """

    provider: str
    model: str | None
    api_key: SecretStr | None
    api_base: str | None

    def key(self) -> str | None:
        """The raw API key string for handing to an SDK, or ``None``."""
        return self.api_key.get_secret_value() if self.api_key is not None else None

    @property
    def speaks_openai(self) -> bool:
        """Whether an OpenAI-compatible client can serve this target.

        ``vllm`` is the OpenAI Chat Completions protocol on a self-hosted
        server: a plugin whose bundled SDK speaks OpenAI serves it by pointing
        that client at :attr:`api_base` (already the ``/v1`` root) with
        :meth:`openai_key`. Treating it as unservable used to drop a vLLM pin
        silently, while the console reported the plugin as pinned.
        """
        return self.provider in OPENAI_WIRE_PROVIDERS

    def openai_key(self) -> str | None:
        """The key an OpenAI SDK client should send for this target.

        vLLM usually runs keyless, but the OpenAI SDKs refuse an empty key, so
        its placeholder stands in; the server ignores it without ``--api-key``.
        Every other provider returns :meth:`key` unchanged.
        """
        key = self.key()
        if key is None and self.provider == "vllm":
            from core.services.llm.providers.vllm_provider import VLLM_NO_KEY

            return VLLM_NO_KEY
        return key


def _vllm_base_for(
    config: object, model: str | None, api_key: SecretStr | None
) -> str | None:
    """The ``/v1`` root of the vLLM server that serves *model*.

    With several servers (``LLM_VLLM_ENDPOINTS``) a plugin's own client must be
    pointed at the one serving the pinned model; with one it is that server.
    ``None`` when no server is configured or none serves *model* — the caller
    then keeps the first configured server, and the call reports the gap.
    """
    from core.services.llm.vllm_endpoints import get_vllm_registry, vllm_endpoints

    endpoints = vllm_endpoints(config)  # type: ignore[arg-type]
    if not endpoints:
        return None
    if not model:
        return endpoints[0]
    key = api_key.get_secret_value() if api_key is not None else None
    return get_vllm_registry().endpoint_for_sync(model, endpoints, key) or endpoints[0]


def resolve_governed_client_config(
    plugin_name: str, scope: str | None = None
) -> GovernedClientConfig | None:
    """Effective provider/model/credentials for *plugin_name*, or ``None``.

    ``None`` means the plugin is unpinned (or the pin is unusable) and should
    keep its own configured defaults — behaviour identical to a deployment with
    no LLM policies. Never raises: any failure degrades to ``None``.

    Args:
        plugin_name: The plugin's registry name (its ``manifest.yaml`` ``name``),
            the same identity the central policy store and the plugin-context
            middleware key on.
        scope: Optional named LLM sub-policy to resolve (one of the plugin's
            declared ``llm_scopes`` ids, e.g. ``"ingestion"`` / ``"chat"``). A
            scope with no pin of its own falls back to the plugin's default pin;
            ``None`` (the default) resolves that plugin-level default directly.

    Returns:
        The governed routing to point the plugin's own SDK client at, or
        ``None`` to keep the plugin's defaults.
    """
    try:
        policy = resolve_plugin_llm_policy(plugin_name, scope)
        if policy is None:
            return None
        config = get_llm_config()
        provider = policy.provider or config.provider
        # Cross-provider pin without a model: the default model belongs to the
        # default provider and would be meaningless (likely a hard error) on
        # another one. Ignore it — mirrors ``runtime._service_for_policy``.
        if policy.provider and policy.provider != config.provider and not policy.model:
            logger.warning(
                "LLM policy for plugin %r pins provider %r without a model "
                "(default model belongs to %r) — ignoring the policy",
                plugin_name,
                policy.provider,
                config.provider,
            )
            return None
        model = policy.model or (config.model if provider == config.provider else None)
        api_key = api_key_for(config, provider)
        api_base = api_base_for(config, provider)
        if provider == "vllm":
            api_base = _vllm_base_for(config, model, api_key) or api_base
        return GovernedClientConfig(
            provider=provider,
            model=model,
            api_key=api_key,
            # Per-provider, never the default provider's URL — see
            # ``core.services.llm.runtime.api_base_for``.
            api_base=api_base,
        )
    except Exception:
        logger.warning(
            "Governed LLM config resolution failed for plugin %r — falling back "
            "to the plugin's own defaults",
            plugin_name,
            exc_info=True,
        )
        return None


__all__ = [
    "OPENAI_WIRE_PROVIDERS",
    "GovernedClientConfig",
    "resolve_governed_client_config",
]
