"""Process-wide LLM service resolution — default singleton + policy clones.

``get_llm_service()`` is the shared funnel every framework consumer calls. It
returns the config-default singleton, unless the current execution context is
bound to a plugin (see :func:`core.context.get_current_plugin`) whose LLM
routing an operator has pinned via the central per-plugin policy seam
(:mod:`core.services.llm.policy`). In that case it returns a cached
:class:`~core.services.llm.service.LLMService` built for the pinned
provider/model — cloned from the central config, so credentials, timeouts,
caching and cost accounting all stay identical to the default service.

Fail-open by design: an unusable policy (unsupported provider, missing
credentials, provider pinned without a model on a cross-provider switch) is
logged and the default service is served instead — central governance must
never break LLM availability.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from core.config import get_llm_config
from core.observability.logging import get_logger
from core.services.llm.policy import PluginLLMPolicy, resolve_active_llm_policy

if TYPE_CHECKING:
    from pydantic import SecretStr

    from core.config.services import LLMConfig
    from core.services.llm.service import LLMService

logger = get_logger(__name__)

# Default singleton + per-(provider, model) policy clones. The clone cache is
# keyed by the *effective* pair so two plugins pinned to the same target share
# one provider client instead of building N.
_default_service: LLMService | None = None
_policy_services: dict[tuple[str, str], LLMService] = {}
_lock = threading.Lock()


def api_key_for(config: LLMConfig, provider: str) -> SecretStr | None:
    """Central credential for *provider*: dedicated key, else the primary one.

    The primary ``LLMConfig.api_key`` belongs to the configured default
    provider, so it is only used when *provider* matches it; other providers
    must supply their dedicated ``<provider>_api_key`` field.
    """
    dedicated: SecretStr | None = {
        "anthropic": config.anthropic_api_key,
        "openai": config.openai_api_key,
        "huggingface": config.huggingface_api_key,
    }.get(provider)
    if dedicated is not None:
        return dedicated
    if provider == config.provider:
        return config.api_key
    return None


def provider_configured(config: LLMConfig, provider: str) -> bool:
    """Whether *provider* has the central credentials/setup it needs to serve.

    Used by admin surfaces to show which providers a policy may pin. Ollama is
    keyless (local endpoint); HuggingFace works keyless in local mode.
    """
    if provider == "ollama":
        return True
    if provider == "huggingface":
        return config.huggingface_local or api_key_for(config, provider) is not None
    return api_key_for(config, provider) is not None


def _get_default_service() -> LLMService:
    """The config-default singleton (created on first use)."""
    global _default_service
    if _default_service is None:
        from core.services.llm.service import LLMService

        _default_service = LLMService()
    return _default_service


def _service_for_policy(policy: PluginLLMPolicy) -> LLMService | None:
    """A cached service honouring *policy*, or ``None`` to use the default.

    Cross-provider pins require an explicit model (the default model belongs
    to the default provider and would be meaningless — likely a hard error —
    on another one), so a provider-only cross pin is ignored.
    """
    base = get_llm_config()
    provider = policy.provider or base.provider
    model = policy.model or base.model
    if policy.provider and policy.provider != base.provider and not policy.model:
        logger.warning(
            "LLM policy pins provider %r without a model (default model %r "
            "belongs to %r) — ignoring the policy",
            policy.provider,
            base.model,
            base.provider,
        )
        return None
    key = (provider, model)
    service = _policy_services.get(key)
    if service is not None:
        return service
    with _lock:
        service = _policy_services.get(key)
        if service is not None:
            return service
        from core.services.llm.service import LLMService

        try:
            config = base.model_copy(
                update={
                    "provider": provider,
                    "model": model,
                    "api_key": api_key_for(base, provider),
                }
            )
            service = LLMService(config=config)
        except Exception as exc:
            logger.warning(
                "LLM policy target %s/%s unusable — serving the default "
                "provider instead: %s",
                provider,
                model,
                exc,
            )
            return None
        if policy.model:
            # A pinned model is governance, not a hint: it also wins over the
            # plugin's own per-call ``model=`` overrides.
            service._pinned_model = policy.model
        _policy_services[key] = service
        return service


def get_llm_service() -> LLMService:
    """Get the LLM service for the current execution context.

    Returns the config-default singleton, or — when the bound plugin has an
    operator-pinned LLM policy — a cached clone routed to the pinned
    provider/model. Never raises on policy problems (falls back to default).

    Returns:
        LLMService: The shared service instance for this context.
    """
    policy = resolve_active_llm_policy()
    if policy is None:
        return _get_default_service()
    return _service_for_policy(policy) or _get_default_service()


def reset_llm_service() -> None:
    """Clear the default singleton and every policy-derived clone.

    Forces fresh initialization on the next :func:`get_llm_service` call —
    for tests, hot-reloading configuration, or after rotating credentials.
    """
    global _default_service
    with _lock:
        _default_service = None
        _policy_services.clear()


__all__ = [
    "api_key_for",
    "get_llm_service",
    "provider_configured",
    "reset_llm_service",
]
