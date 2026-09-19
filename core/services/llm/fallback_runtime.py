"""Cross-provider fallback wiring for the LLM service.

Builds a :class:`core.models.fallback.FallbackChain` around the primary
provider call plus config-declared ``provider:model`` fallback stages
(``LLMConfig.fallback_chain``). Each fallback stage is a cached
:class:`~core.services.llm.service.LLMService` clone (same config surface,
dedicated credentials via :func:`core.services.llm.runtime.api_key_for`), so
timeouts and retry discipline stay identical to the primary path.

Open circuit breakers are skipped without paying for a doomed call. Budget
and deadline errors are fatal: the request is out of money or time, so
falling through to a second provider would double-spend, not recover. So is a
policy **refusal**: the model ran, was billed and declined — re-asking a
different model the thing the first refused is provider shopping, not failover,
and burying the refusal inside ``AllProvidersFailedError`` would also hide it
from the refusal accounting that books the spend. So, finally, is a rejected
**request** (:class:`~core.services.llm.errors.LLMClientError`: an expired or
wrong key, an unknown model, a malformed payload): the primary never ran, and
nothing about a second provider fixes a misconfiguration. Falling through
there is the failure mode this chain exists to prevent rather than cause — a
deployment whose hosted key has lapsed quietly serves every request from a
local model instead, indefinitely, and the only symptom is a warning nobody
reads. The chain covers outages, not configuration.

Whichever stage answers is counted (``mas_llm_fallback_served_total``) and
logged with the trail of failures behind it, so "we are running on the
fallback" is an alertable fact rather than an archaeology exercise.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from core.models.fallback import AllProvidersFailedError, FallbackChain, Provider
from core.services.llm._fallback_support import (
    _breaker_open,
    _chain_timeout,
    _clone_service,
    _settle,
    _stage_name,
    _stage_timeout,
    fatal_exception_types,
)
from core.services.llm._fallback_support import (
    parse_fallback_chain as parse_fallback_chain,  # re-export: see below
)
from core.services.llm._fallback_support import (
    reset_fallback_services as reset_fallback_services,  # re-export: see below
)
from core.services.llm.exceptions import LLMProviderError

# ``parse_fallback_chain`` and ``reset_fallback_services`` moved to
# ``_fallback_support`` when this module was split, but ``core.services.llm``
# exports them from here and callers import them from here. Re-exported under
# their own names so both spellings keep resolving.

if TYPE_CHECKING:
    from core.services.llm.service import LLMService


async def maybe_run_with_fallback(
    service: LLMService,
    prompt: str,
    model: str,
    json_mode: bool,
    **kwargs: object,
) -> tuple[str, int, str, str]:
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
    return content, tokens, service.config.provider, model


async def run_with_fallback(
    service: LLMService,
    prompt: str,
    model: str,
    json_mode: bool,
    **kwargs: object,
) -> tuple[str, int, str, str]:
    """Run the primary provider, falling through the configured chain on failure.

    Returns:
        tuple: ``(content, tokens_used, serving_provider, serving_model)`` so
        the caller can attribute metrics **and cost** to what actually served
        the request. Both halves matter: billing a fallback answer to the
        primary's model prices local inference at a hosted rate, which is
        spend the deployment never made.
    """
    primary_name = service.config.provider

    async def _primary() -> tuple[str, int]:
        result: tuple[str, int] = await service._generate_with_retry(
            prompt=prompt, model=model, json_mode=json_mode, **kwargs
        )
        return result

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
            staged: tuple[str, int] = await clone._generate_with_retry(
                prompt=prompt, model=_model, json_mode=json_mode, **kwargs
            )
            return staged

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
        total_timeout_seconds=_chain_timeout(service),
        fatal_exceptions=fatal_exception_types(),
    )
    try:
        outcome = await chain.run()
    except AllProvidersFailedError as exc:
        raise LLMProviderError(str(exc)) from exc
    served_by, served_model = _settle(outcome, primary_name, model, "text")
    content, tokens = outcome.result
    return content, tokens, served_by, served_model


async def maybe_run_structured_with_fallback(
    service: LLMService,
    prompt: str,
    model: str,
    *,
    tools: object = None,
    tool_choice: object = None,
    response_format: object = None,
    **kwargs: object,
) -> tuple[object, str, str]:
    """Fallback-aware **native structured** call (tool calling / typed output).

    Same chain discipline as :func:`maybe_run_with_fallback`, applied to
    ``generate_structured``: direct provider call when no chain is set;
    otherwise primary + config-declared stages, skipping open breakers and
    stages whose provider lacks native tool support (a coercion stage would
    silently change semantics mid-chain). Budget/deadline errors stay fatal.

    Returns:
        tuple: ``(LLMResult, serving_provider, serving_model)``.
    """
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
        return await _primary(), primary_name, model

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
        total_timeout_seconds=_chain_timeout(service),
        fatal_exceptions=fatal_exception_types(),
    )
    try:
        outcome = await chain.run()
    except AllProvidersFailedError as exc:
        raise LLMProviderError(str(exc)) from exc
    served_by, served_model = _settle(outcome, primary_name, model, "structured")
    return outcome.result, served_by, served_model


async def maybe_run_messages_with_fallback(
    service: LLMService,
    messages: object,
    model: str,
    **kwargs: object,
) -> tuple[object, str, str]:
    """Fallback-aware **message API** call (the agentic conversation path).

    Same chain discipline as :func:`maybe_run_structured_with_fallback`, applied
    to ``generate_messages``: direct provider call when no chain is set;
    otherwise primary + config-declared stages, skipping open breakers and
    stages whose provider has no message API. That last filter is the same
    reasoning as the structured one's native-tool check, one step stricter: a
    stage that cannot receive a message list would have to be handed a
    flattened transcript, silently dropping tool-call correlation, ``is_error``
    and thinking blocks mid-chain — a different conversation answering the same
    request. Budget/deadline errors stay fatal.

    Args:
        service: The primary :class:`~core.services.llm.service.LLMService`.
        messages: The neutral history (``list[Message]``).
        model: Model for the primary stage.
        **kwargs: ``tools``, ``system``, ``tool_choice``, ``response_format``
            and the provider's usual parameters.

    Returns:
        tuple: ``(LLMResult, serving_provider, serving_model)``.
    """
    from core.services.llm.message_runtime import _messages_with_retry

    primary_name = service.config.provider
    chain_spec = getattr(service.config, "fallback_chain", "")

    async def _primary() -> object:
        return await _messages_with_retry(service, messages, model, **kwargs)  # type: ignore[arg-type]

    if not (isinstance(chain_spec, str) and chain_spec):
        return await _primary(), primary_name, model

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
            if not getattr(clone.provider, "supports_messages", False):
                raise LLMProviderError(
                    f"Fallback provider '{_provider}' has no message API"
                )
            return await _messages_with_retry(clone, messages, _model, **kwargs)  # type: ignore[arg-type]

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
        total_timeout_seconds=_chain_timeout(service),
        fatal_exceptions=fatal_exception_types(),
    )
    try:
        outcome = await chain.run()
    except AllProvidersFailedError as exc:
        raise LLMProviderError(str(exc)) from exc
    served_by, served_model = _settle(outcome, primary_name, model, "messages")
    return outcome.result, served_by, served_model
