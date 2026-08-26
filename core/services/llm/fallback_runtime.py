"""Cross-provider fallback wiring for the LLM service.

Builds a :class:`core.models.fallback.FallbackChain` around the primary
provider call plus config-declared ``provider:model`` fallback stages
(``LLMConfig.fallback_chain``). Each fallback stage is a cached
:class:`~core.services.llm.service.LLMService` clone (same config surface,
dedicated credentials via :func:`core.services.llm.runtime.api_key_for`), so
timeouts and retry discipline stay identical to the primary path.

Open circuit breakers are skipped without paying for a doomed call. Budget
and deadline errors are fatal: the request is out of money or time, so
falling through to a second provider would double-spend, not recover.
"""

from __future__ import annotations

import threading
from functools import partial
from typing import TYPE_CHECKING

from core.models.fallback import AllProvidersFailedError, FallbackChain, Provider
from core.observability.logging import get_logger
from core.resilience.circuit_breaker import CircuitState, get_circuit_breaker

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

logger = get_logger(__name__)

_SUPPORTED_PROVIDERS = ("openai", "ollama", "huggingface", "anthropic", "gemini")

# Fallback-stage service clones, shared process-wide and keyed by
# (provider, model) — mirrors the policy-clone cache in ``runtime``.
_fallback_services: dict[tuple[str, str], LLMService] = {}
_lock = threading.Lock()


def parse_fallback_chain(spec: str) -> list[tuple[str, str]]:
    """Parse ``LLMConfig.fallback_chain`` into ordered (provider, model) pairs."""
    entries: list[tuple[str, str]] = []
    for raw in spec.split(","):
        item = raw.strip()
        if not item:
            continue
        provider, sep, model = item.partition(":")
        provider, model = provider.strip(), model.strip()
        if not sep or not provider or not model:
            raise ValueError(
                f"Malformed fallback entry {item!r}: expected 'provider:model'"
            )
        if provider not in _SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported fallback provider: {provider}")
        entries.append((provider, model))
    return entries


def _breaker_open(provider: str) -> bool:
    """Whether *provider*'s circuit breaker is currently OPEN."""
    return get_circuit_breaker(f"{provider}_provider").state == CircuitState.OPEN


def _clone_service(base: LLMService, provider: str, model: str) -> LLMService:
    """A cached LLMService clone for a fallback stage (built on first use)."""
    key = (provider, model)
    service = _fallback_services.get(key)
    if service is not None:
        return service
    with _lock:
        service = _fallback_services.get(key)
        if service is not None:
            return service
        from core.services.llm.runtime import api_base_for, api_key_for
        from core.services.llm.service import LLMService

        config = base.config.model_copy(
            update={
                "provider": provider,
                "model": model,
                "api_key": api_key_for(base.config, provider),
                # Endpoints are per-provider. Carrying the primary's URL into a
                # fallback stage aims it at the wrong server — the classic
                # shape being a hosted default with ``ollama:…`` behind it,
                # where the local stage would dial the hosted gateway and
                # stall until the read timeout.
                "api_base": api_base_for(base.config, provider),
                # A clone must never recurse into its own fallback chain.
                "fallback_chain": "",
            }
        )
        service = LLMService(config=config, enable_cache=False)
        _fallback_services[key] = service
        return service


def _stage_name(provider: str, model: str) -> str:
    """A unique stage id for the chain, and the provider it maps back to.

    ``FallbackChain`` requires distinct stage names, and naming a stage after
    its provider alone made a perfectly ordinary chain illegal: a primary on
    ``ollama`` with ``ollama:<smaller-model>`` behind it — big model first,
    cheap model as the safety net — collided with the primary and raised
    ``duplicate provider names in chain`` on *every* call, turning a fallback
    into a total outage. The provider stays the breaker key (a rate limit is a
    property of the provider, not of one model); only the stage id is widened.
    """
    return f"{provider}:{model}"


def _stage_provider(stage_name: str) -> str:
    """The provider id behind a stage name, for metrics and log attribution."""
    return stage_name.split(":", 1)[0]


def _stage_timeout(service: LLMService) -> float | None:
    """The per-stage bound for this service's chain, or ``None`` for unbounded.

    Read defensively: the ``isinstance`` guard mirrors the one in
    :func:`maybe_run_with_fallback` — a Mock/SimpleNamespace test config
    answers every attribute with a truthy object, which would otherwise arm a
    timeout of "some Mock" on every test that touches this path.
    """
    value = getattr(service.config, "fallback_stage_timeout", None)
    return value if isinstance(value, (int, float)) and value > 0 else None


def reset_fallback_services() -> None:
    """Clear the fallback-stage clone cache (tests / credential rotation)."""
    with _lock:
        _fallback_services.clear()


async def maybe_run_with_fallback(
    service: LLMService,
    prompt: str,
    model: str,
    json_mode: bool,
    **kwargs: object,
) -> tuple[str, int, str]:
    """Fallback-aware generate: direct provider call when no chain is set.

    The ``isinstance`` guard keeps Mock/SimpleNamespace test configs (whose
    attributes are truthy objects) from accidentally enabling fallback.
    """
    chain_spec = getattr(service.config, "fallback_chain", "")
    if isinstance(chain_spec, str) and chain_spec:
        return await run_with_fallback(
            service, prompt=prompt, model=model, json_mode=json_mode, **kwargs
        )
    content, tokens = await service._generate_with_retry(
        prompt=prompt, model=model, json_mode=json_mode, **kwargs
    )
    return content, tokens, service.config.provider


async def run_with_fallback(
    service: LLMService,
    prompt: str,
    model: str,
    json_mode: bool,
    **kwargs: object,
) -> tuple[str, int, str]:
    """Run the primary provider, falling through the configured chain on failure.

    Returns ``(content, tokens_used, serving_provider_name)`` so the caller
    can attribute metrics to the provider that actually served the request.
    """
    from core.middleware.cost_control import (
        BudgetExceededError as MiddlewareBudgetExceededError,
    )
    from core.orchestration.limits import BudgetExceededError as LoopBudgetExceededError
    from core.services.llm.exceptions import BudgetExceededError, LLMProviderError

    primary_name = service.config.provider

    async def _primary() -> tuple[str, int]:
        return await service._generate_with_retry(
            prompt=prompt, model=model, json_mode=json_mode, **kwargs
        )

    stages: list[Provider[tuple[str, int]]] = [
        Provider(
            name=_stage_name(primary_name, model),
            call=_primary,
            is_open=lambda: _breaker_open(primary_name),
        )
    ]
    for fb_provider, fb_model in parse_fallback_chain(service.config.fallback_chain):
        if fb_provider == primary_name and fb_model == model:
            continue  # identical to the primary stage — nothing to gain

        async def _stage(
            _provider: str = fb_provider, _model: str = fb_model
        ) -> tuple[str, int]:
            clone = _clone_service(service, _provider, _model)
            return await clone._generate_with_retry(
                prompt=prompt, model=_model, json_mode=json_mode, **kwargs
            )

        stages.append(
            Provider(
                name=_stage_name(fb_provider, fb_model),
                call=_stage,
                is_open=partial(_breaker_open, fb_provider),
            )
        )

    chain: FallbackChain[tuple[str, int]] = FallbackChain(
        stages,
        stage_timeout_seconds=_stage_timeout(service),
        fatal_exceptions=(
            BudgetExceededError,
            MiddlewareBudgetExceededError,
            LoopBudgetExceededError,
        ),
    )
    try:
        outcome = await chain.run()
    except AllProvidersFailedError as exc:
        raise LLMProviderError(str(exc)) from exc
    served_by = _stage_provider(outcome.provider)
    if outcome.provider != _stage_name(primary_name, model):
        logger.warning(
            "llm_fallback_served",
            extra={"provider": served_by, "primary": primary_name},
        )
    content, tokens = outcome.result
    return content, tokens, served_by


async def maybe_run_structured_with_fallback(
    service: LLMService,
    prompt: str,
    model: str,
    *,
    tools: object = None,
    tool_choice: object = None,
    response_format: object = None,
    **kwargs: object,
) -> tuple[object, str]:
    """Fallback-aware **native structured** call (tool calling / typed output).

    Same chain discipline as :func:`maybe_run_with_fallback`, applied to
    ``generate_structured``: direct provider call when no chain is set;
    otherwise primary + config-declared stages, skipping open breakers and
    stages whose provider lacks native tool support (a coercion stage would
    silently change semantics mid-chain). Budget/deadline errors stay fatal.

    Returns ``(LLMResult, serving_provider_name)``.
    """
    from core.middleware.cost_control import (
        BudgetExceededError as MiddlewareBudgetExceededError,
    )
    from core.orchestration.limits import BudgetExceededError as LoopBudgetExceededError
    from core.services.llm.exceptions import BudgetExceededError, LLMProviderError
    from core.services.llm.structured import _native_with_retry

    primary_name = service.config.provider
    chain_spec = getattr(service.config, "fallback_chain", "")

    async def _primary() -> object:
        return await _native_with_retry(
            service,
            prompt,
            model,
            tools=tools,  # type: ignore[arg-type]
            tool_choice=tool_choice,  # type: ignore[arg-type]
            response_format=response_format,  # type: ignore[arg-type]
            **kwargs,
        )

    if not (isinstance(chain_spec, str) and chain_spec):
        return await _primary(), primary_name

    stages: list[Provider[object]] = [
        Provider(
            name=_stage_name(primary_name, model),
            call=_primary,
            is_open=lambda: _breaker_open(primary_name),
        )
    ]
    for fb_provider, fb_model in parse_fallback_chain(chain_spec):
        if fb_provider == primary_name and fb_model == model:
            continue

        async def _stage(
            _provider: str = fb_provider, _model: str = fb_model
        ) -> object:
            clone = _clone_service(service, _provider, _model)
            if not getattr(clone.provider, "supports_native_tools", False):
                raise LLMProviderError(
                    f"Fallback provider '{_provider}' has no native structured API"
                )
            return await _native_with_retry(
                clone,
                prompt,
                _model,
                tools=tools,  # type: ignore[arg-type]
                tool_choice=tool_choice,  # type: ignore[arg-type]
                response_format=response_format,  # type: ignore[arg-type]
                **kwargs,
            )

        stages.append(
            Provider(
                name=_stage_name(fb_provider, fb_model),
                call=_stage,
                is_open=partial(_breaker_open, fb_provider),
            )
        )

    chain: FallbackChain[object] = FallbackChain(
        stages,
        stage_timeout_seconds=_stage_timeout(service),
        fatal_exceptions=(
            BudgetExceededError,
            MiddlewareBudgetExceededError,
            LoopBudgetExceededError,
        ),
    )
    try:
        outcome = await chain.run()
    except AllProvidersFailedError as exc:
        raise LLMProviderError(str(exc)) from exc
    served_by = _stage_provider(outcome.provider)
    if outcome.provider != _stage_name(primary_name, model):
        logger.warning(
            "llm_structured_fallback_served",
            extra={"provider": served_by, "primary": primary_name},
        )
    return outcome.result, served_by
