"""Body of :meth:`LLMService.generate_response`.

Split out of ``service.py`` for the module size cap, following the same
pattern as ``_streaming`` and ``structured``. Holds the traced, cached,
single-flighted text-generation path: cache lookups, token accounting,
GenAI span attributes and budget charging.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any

from core.middleware.cost_control import (
    BudgetExceededError as MiddlewareBudgetExceededError,
)
from core.observability.logging import get_logger
from core.quotas.manager import CostBudgetExceededError
from core.services.llm._telemetry import (
    gen_ai_system,
    record_genai_metrics,
    report_tokens_to_middleware,
)
from core.services.llm.cost_control import estimate_tokens_async
from core.services.llm.exceptions import BudgetExceededError, LLMProviderError
from core.services.llm.fallback_runtime import maybe_run_with_fallback

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

logger = get_logger(__name__)


def _resolve_effort(service: LLMService, effort: str | None, task_category: str | None):
    """Explicit effort wins; otherwise derive it from the task category.

    Returns None when extended thinking is disabled or maps to OFF, in which
    case no ``effort`` kwarg reaches the provider and behaviour is unchanged.
    """
    if effort is not None or not getattr(service.config, "thinking_enabled", False):
        return effort

    from core.services.llm.thinking import EffortLevel, effort_for_category

    derived = effort_for_category(task_category)
    if derived is not None and derived is not EffortLevel.OFF:
        return derived.value
    return None


def _build_span_attributes(
    service: LLMService,
    *,
    model: str,
    prompt: str,
    json_mode: bool,
    temperature: float | None,
    max_tokens: int | None,
) -> dict[str, Any]:
    """OTel GenAI semantic conventions (``gen_ai.*``) so standard GenAI
    dashboards and semconv-aware backends light up. App-specific fields live
    under the ``gen_ai.baselith.*`` extension namespace."""
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.system": gen_ai_system(service.config.provider),
        "gen_ai.request.model": model,
        "gen_ai.baselith.json_mode": json_mode,
        "gen_ai.baselith.prompt_length": len(prompt),
    }
    if temperature is not None:
        attributes["gen_ai.request.temperature"] = temperature
    if max_tokens is not None:
        attributes["gen_ai.request.max_tokens"] = max_tokens
    return attributes


def _build_cache_key(
    *,
    model: str,
    prompt: str,
    json_mode: bool,
    system_prompt: str | None,
    temperature: float | None,
    max_tokens: int | None,
    effort: str | None,
) -> tuple[str, str]:
    """Return ``(cache_key, prompt_hash)``.

    The hash covers every input that can change the completion (system prompt
    and sampling params, not just the user prompt) so two callers with the same
    prompt but different system prompts never share a cached answer.
    """
    from core.context import get_current_tenant_id

    tenant_id = get_current_tenant_id()
    key_material = "\x1f".join(
        (prompt, system_prompt or "", repr(temperature), repr(max_tokens))
    )
    if effort is not None:
        # Thinking effort changes the completion; keep legacy keys (and warm
        # caches) intact for calls without it.
        key_material += f"\x1feffort={effort}"
    prompt_hash = hashlib.sha256(key_material.encode()).hexdigest()
    return f"{tenant_id}:{model}:{json_mode}:{prompt_hash}", prompt_hash


async def generate_response(
    service: LLMService,
    prompt: str,
    model: str | None = None,
    json: bool = False,
    system_prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    task_category: str | None = None,
    effort: str | None = None,
) -> str:
    """Run the cached, traced text-generation path for *service*.

    See :meth:`core.services.llm.service.LLMService.generate_response` for the
    argument contract; this is its implementation.
    """
    from core.observability import get_tracer

    # Lazy: a module-level import of core.orchestration would be circular
    # (orchestration handlers import this service).
    from core.orchestration.limits import (
        BudgetExceededError as LoopBudgetExceededError,
    )

    # Bound to a fresh non-optional name: the nested `_generate_and_cache`
    # closure reads it, and mypy does not carry assignment narrowing into a
    # nested function — it would still see the parameter's `str | None`.
    resolved_model: str = service._resolve_model(model, task_category)
    effort = _resolve_effort(service, effort, task_category)

    tracer = get_tracer("llm-service")
    span_attributes = _build_span_attributes(
        service,
        model=resolved_model,
        prompt=prompt,
        json_mode=json,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    with tracer.start_span(
        f"chat {resolved_model}", attributes=span_attributes
    ) as span:
        # Cheapest-first: the exact cache is an O(1) Redis GET, while the
        # semantic cache runs a sentence-transformer inference to embed the
        # prompt. Check the exact cache before the semantic one so an exact hit
        # never pays for an embedding it doesn't need.
        cache_key, prompt_hash = _build_cache_key(
            model=resolved_model,
            prompt=prompt,
            json_mode=json,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            effort=effort,
        )
        if service.cache is not None:
            cached = await service.cache.get(cache_key)
            if cached:
                logger.debug("Cache hit for prompt hash: %s", prompt_hash[:16])
                span.set_attribute("gen_ai.baselith.cache_hit", True)
                return cached

        # Semantic cache (approximate match) only on exact miss.
        if service.semantic_cache is not None:
            semantic_cached = await service.semantic_cache.get_similar(prompt)
            if semantic_cached:
                span.set_attribute("gen_ai.baselith.semantic_cache_hit", True)
                return semantic_cached

        span.set_attribute("gen_ai.baselith.cache_hit", False)
        span.set_attribute("gen_ai.baselith.semantic_cache_hit", False)

        async def _generate_and_cache() -> str:
            # Re-check the cache after acquiring the single-flight slot: an
            # earlier concurrent caller may have populated it while we were
            # queued, in which case we skip the upstream call.
            if service.cache is not None:
                fresh = await service.cache.get(cache_key)
                if fresh:
                    span.set_attribute("gen_ai.baselith.cache_hit", True)
                    return fresh

            # Gate on the ambient tenant's cumulative USD budget BEFORE any
            # provider spend (no-op unless tenant cost limits are configured;
            # fails open on store errors).
            from core.quotas.cost_enforcement import enforce_tenant_cost_budget

            await enforce_tenant_cost_budget()

            # Track input tokens (large prompts encode off the event loop)
            input_tokens = await estimate_tokens_async(prompt)
            report_tokens_to_middleware(input_tokens, model="input")
            if service.cost_tracker:
                service.cost_tracker.track_tokens(input_tokens, model="input")

            extra_kwargs: dict = {}
            if system_prompt:
                extra_kwargs["system"] = system_prompt
            if temperature is not None:
                extra_kwargs["temperature"] = temperature
            if max_tokens is not None:
                extra_kwargs["max_tokens"] = max_tokens
            if effort is not None:
                extra_kwargs["effort"] = effort
                span.set_attribute("gen_ai.baselith.thinking_effort", effort)
            started = time.perf_counter()
            content, tokens_used, serving_provider = await maybe_run_with_fallback(
                service,
                prompt=prompt,
                model=resolved_model,
                json_mode=json,
                **extra_kwargs,
            )

            output_tokens = max(tokens_used - input_tokens, 0)
            span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
            span.set_attribute("gen_ai.baselith.response_length", len(content))
            span.set_attribute("gen_ai.baselith.serving_provider", serving_provider)

            # Opt-in OpenInference enrichment (Phoenix/Arize-style backends)
            # on the same span; content capture is a second opt-in.
            from core.observability.openinference import openinference_llm_attributes

            for key, value in openinference_llm_attributes(
                model=resolved_model,
                provider=serving_provider,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                prompt=prompt,
                completion=content,
            ).items():
                span.set_attribute(key, value)
            report_tokens_to_middleware(output_tokens, model=resolved_model)
            if service.cost_tracker:
                service.cost_tracker.track_tokens(output_tokens, model=resolved_model)
            record_genai_metrics(
                gen_ai_system(serving_provider),
                resolved_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_seconds=time.perf_counter() - started,
            )

            # Charge real dollar cost against the ambient per-request
            # LoopBudget (no-op outside an orchestrated request). Raises
            # LoopBudgetExceededError when the request blows its USD cap.
            from core.orchestration.budget_context import charge_llm_cost

            charge_llm_cost(resolved_model, input_tokens, output_tokens)

            # Book the cost on the tenant's cumulative ledger (enforced by
            # the pre-call gate above on the NEXT call; never raises).
            # Priced independently of the LoopBudget charge, which returns 0
            # outside an orchestrated request — background jobs meter too.
            from core.quotas.cost_enforcement import (
                llm_call_cost_usd,
                record_tenant_llm_cost,
            )

            await record_tenant_llm_cost(
                llm_call_cost_usd(resolved_model, input_tokens, output_tokens)
            )

            # Cache response (exact match)
            if service.cache is not None:
                await service.cache.set(cache_key, content)

            # Cache response (semantic)
            if service.semantic_cache is not None:
                await service.semantic_cache.set(prompt, content)

            return content

        try:
            return await service._inflight.do(cache_key, _generate_and_cache)
        except (
            BudgetExceededError,
            MiddlewareBudgetExceededError,
            LoopBudgetExceededError,
        ):
            span.set_attribute("gen_ai.baselith.error", "budget_exceeded")
            raise
        except CostBudgetExceededError:
            span.set_attribute("gen_ai.baselith.error", "tenant_cost_budget_exceeded")
            raise
        except Exception as e:
            span.set_attribute("gen_ai.baselith.error", str(e))
            logger.error(f"Error generating response: {e}")
            raise LLMProviderError(f"Generation failed: {e}") from e


__all__ = ["generate_response"]
