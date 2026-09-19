"""Shared machinery behind the LLM fallback chain.

Split out of :mod:`core.services.llm.fallback_runtime`, which owns the four
public entry points (text, structured, messages, and — via
:mod:`core.services.llm._stream_fallback` — streaming). Everything those paths
have in common lives here: parsing the configured chain, skipping open
breakers, building and caching a stage's service clone, bounding a stage and
the chain, the set of exceptions that abort instead of falling through, and
the accounting of which stage actually answered.

One definition each, on purpose: the four paths previously repeated the fatal
set and the "served by a fallback" log inline, and a rule added to one of them
(a new fatal class, a counter) silently did not apply to the other three.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from core.models.fallback import FallbackOutcome, ProviderAttempt
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


def _stage_model(stage_name: str) -> str:
    """The model id behind a stage name.

    A model name may itself contain colons (``qwen2.5:7b-instruct``), so the
    split is bounded: everything after the FIRST colon is the model.
    """
    return stage_name.split(":", 1)[1]


def fatal_exception_types() -> tuple[type[BaseException], ...]:
    """Exceptions that abort the whole chain instead of trying the next stage.

    Budget and deadline overruns: the request has no money or no time left, so
    a second provider would double-spend rather than recover. A refusal: the
    model ran, was billed, and declined. A client error: the request itself was
    rejected (bad key, unknown model, malformed payload), which a different
    provider cannot fix — and which, left fallible, silently converts an
    expired hosted credential into permanent local inference.

    Imported lazily: ``core.middleware`` and ``core.orchestration`` both reach
    back into this package.
    """
    from core.middleware.cost_control import (
        BudgetExceededError as MiddlewareBudgetExceededError,
    )
    from core.orchestration.limits import BudgetExceededError as LoopBudgetExceededError
    from core.services.llm.errors import LLMClientError, LLMRefusalError
    from core.services.llm.exceptions import BudgetExceededError

    return (
        BudgetExceededError,
        MiddlewareBudgetExceededError,
        LoopBudgetExceededError,
        LLMRefusalError,
        LLMClientError,
    )


def record_fallback_served(
    *,
    primary: str,
    served_by: str,
    served_model: str,
    path: str,
    attempts: list[ProviderAttempt] | None = None,
) -> None:
    """Count and log a call that a fallback stage answered.

    The request succeeded, so this is a degradation and not an error — but one
    a deployment must be able to alert on, because a chain that quietly carries
    production traffic is indistinguishable from a healthy primary until the
    bill (or the GPU) says otherwise. The counter is the alertable signal; the
    log line carries why each earlier stage failed.

    Args:
        primary: The configured primary provider that did not answer.
        served_by: The provider that did.
        served_model: The model that provider was asked for.
        path: Which funnel path ran (``text``/``structured``/``messages``/
            ``stream``), so one chain's behaviour can be read per surface.
        attempts: The chain's attempt trail, when the caller has one.
    """
    try:
        from core.observability.metrics import LLM_FALLBACK_SERVED_TOTAL

        LLM_FALLBACK_SERVED_TOTAL.labels(primary, served_by, path).inc()
    except Exception:  # silent-ok: a metrics registry must never fail a request
        pass
    logger.warning(
        "llm_fallback_served",
        extra={
            "provider": served_by,
            "model": served_model,
            "primary": primary,
            "path": path,
            "failed_stages": [
                f"{a.provider}: {a.error}"
                for a in (attempts or [])
                if not a.succeeded and a.error
            ],
        },
    )


def _settle(
    outcome: FallbackOutcome[object], primary_name: str, model: str, path: str
) -> tuple[str, str]:
    """Attribute an outcome: ``(serving_provider, serving_model)``.

    Reports the fallback when one served, so every path counts it identically.
    """
    served_by = _stage_provider(outcome.provider)
    served_model = _stage_model(outcome.provider)
    if outcome.provider != _stage_name(primary_name, model):
        record_fallback_served(
            primary=primary_name,
            served_by=served_by,
            served_model=served_model,
            path=path,
            attempts=outcome.attempts,
        )
    return served_by, served_model


def _stage_timeout(service: LLMService) -> float | None:
    """The per-stage bound for this service's chain, or ``None`` for unbounded.

    Read defensively: the ``isinstance`` guard mirrors the one in
    :func:`maybe_run_with_fallback` — a Mock/SimpleNamespace test config
    answers every attribute with a truthy object, which would otherwise arm a
    timeout of "some Mock" on every test that touches this path.
    """
    value = getattr(service.config, "fallback_stage_timeout", None)
    return value if isinstance(value, (int, float)) and value > 0 else None


def _chain_timeout(service: LLMService) -> float | None:
    """Wall-clock budget for the whole chain.

    Falls back to ``request_timeout``: without a ceiling the chain can run for
    stage_count x (request_timeout x retry attempts + backoff), which is many
    minutes — far past the point the caller gave up. Read defensively for the
    same reason as :func:`_stage_timeout`.
    """
    value = getattr(service.config, "fallback_total_timeout", None)
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    request_timeout = getattr(service.config, "request_timeout", None)
    if isinstance(request_timeout, (int, float)) and request_timeout > 0:
        return float(request_timeout)
    return None


def reset_fallback_services() -> None:
    """Clear the fallback-stage clone cache (tests / credential rotation)."""
    with _lock:
        _fallback_services.clear()
