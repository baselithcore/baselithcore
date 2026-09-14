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
from core.services.llm._accounting import (
    charge_usage_to_budget,
    record_usage_cost,
    set_usage_span_attributes,
)
from core.services.llm._telemetry import (
    gen_ai_system,
    record_genai_metrics,
    report_tokens_to_middleware,
)
from core.services.llm.cost_control import estimate_tokens_async
from core.services.llm.errors import LLMRefusalError
from core.services.llm.exceptions import BudgetExceededError, LLMProviderError
from core.services.llm.fallback_runtime import maybe_run_with_fallback
from core.services.llm.usage import Usage, billed_usage

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

logger = get_logger(__name__)


def _resolve_effort(
    service: LLMService, effort: str | None, task_category: str | None
) -> str | None:
    """Explicit effort wins; otherwise derive it from the task category.

    Returns None when extended thinking is disabled or maps to OFF, in which
    case no ``effort`` kwarg reaches the provider and behaviour is unchanged.
    """
    if effort is not None or not getattr(service.config, "thinking_enabled", False):
        return effort

    from core.services.llm.thinking import EffortLevel, effort_for_category

    derived = effort_for_category(task_category)
    if derived is not None and derived is not EffortLevel.OFF:
        return str(derived.value)
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


async def _account_refusal(
    service: LLMService,
    span: Any,
    *,
    model: str,
    usage: Usage,
    input_tokens: int,
    started: float,
) -> None:
    """Book a refused turn against every ledger before the error propagates.

    Args:
        service: The owning :class:`LLMService`.
        span: The active generation span.
        model: The model that refused.
        usage: What the provider metered before refusing (possibly empty).
        input_tokens: The prompt estimate already booked pre-call.
        started: ``time.perf_counter()`` at the start of the call.
    """
    from core.services.llm._accounting import account_turn
    from core.services.llm.stop_reasons import STOP_REFUSAL
    from core.services.llm.tool_calling import LLMResult

    billed = account_turn(
        service,
        span,
        model=model,
        result=LLMResult(stop_reason=STOP_REFUSAL, usage=usage),
        input_tokens=input_tokens,
        started=started,
    )
    await record_usage_cost(model, billed)


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
    from core.context import get_tenant_or_default

    # Lenient on purpose: the tenant only *namespaces* the cache key, it is not
    # an access boundary (the entry is keyed by the prompt hash and never read
    # across prefixes). Under ``strict_tenant_isolation`` the strict lookup
    # raised here for every out-of-request caller — a plugin's background task,
    # a scheduler, a CLI script — killing the whole generation before the
    # provider was ever called. Unbound callers share the ``"default"`` bucket,
    # exactly like the cost ledger (``enforce_tenant_cost_budget``) does.
    tenant_id = get_tenant_or_default()
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
    allow_refusal: bool = False,
    usage_sink: list[Usage] | None = None,
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
                return str(semantic_cached)

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

            await enforce_tenant_cost_budget(model=resolved_model)

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
            if allow_refusal:
                extra_kwargs["allow_refusal"] = True
            # Providers that meter usage publish the exact per-bucket split
            # here; the ``(text, total)`` return type cannot carry it, and
            # re-deriving output as "total minus an estimated prompt"
            # misprices every call (output bills at up to 5x input).
            provider_usage: list[Usage] = []
            extra_kwargs["usage_sink"] = provider_usage
            started = time.perf_counter()
            try:
                content, tokens_used, serving_provider = await maybe_run_with_fallback(
                    service,
                    prompt=prompt,
                    model=resolved_model,
                    json_mode=json,
                    **extra_kwargs,
                )
            except LLMRefusalError:
                # A refusal is generated output: the model ran and the call was
                # billed. Book it before the error propagates — the same policy
                # the structured and streamed paths apply — or the spend
                # disappears from the ledgers for every one of this function's
                # callers.
                await _account_refusal(
                    service,
                    span,
                    model=resolved_model,
                    usage=provider_usage[-1] if provider_usage else Usage(),
                    input_tokens=input_tokens,
                    started=started,
                )
                raise

            # Middleware and the cost tracker keep a running total: the prompt
            # estimate was already booked pre-call, so only the remainder may
            # be added. Pricing and telemetry use the metered split instead.
            output_tokens = max(tokens_used - input_tokens, 0)
            metered = provider_usage[-1] if provider_usage else None
            billed = billed_usage(
                metered,
                fallback_input=input_tokens,
                fallback_total=tokens_used,
            )
            if usage_sink is not None:
                # The caller asked to cost this call: hand back the provider's
                # record, or an explicitly-flagged estimate when it reported
                # nothing, so the caller can tell the two apart.
                usage_sink.append(metered if metered is not None else billed)
            set_usage_span_attributes(span, billed)
            span.set_attribute("gen_ai.baselith.response_length", len(content))
            span.set_attribute("gen_ai.baselith.serving_provider", serving_provider)

            # Opt-in OpenInference enrichment (Phoenix/Arize-style backends)
            # on the same span; content capture is a second opt-in.
            from core.observability.openinference import openinference_llm_attributes

            for key, value in openinference_llm_attributes(
                model=resolved_model,
                provider=serving_provider,
                # OpenInference has no cache tiers: its prompt count means
                # "tokens in the prompt", so it gets the whole prompt side
                # (the cost split lives in the gen_ai.* attributes above).
                input_tokens=billed.prompt_tokens,
                output_tokens=billed.output_tokens,
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
                input_tokens=billed.input_tokens,
                output_tokens=billed.output_tokens,
                cache_read_tokens=billed.cache_read_tokens,
                cache_write_tokens=billed.cache_write_tokens,
                duration_seconds=time.perf_counter() - started,
            )

            # Charge real dollar cost against the ambient per-request
            # LoopBudget (no-op outside an orchestrated request). Raises
            # LoopBudgetExceededError when the request blows its USD cap.
            # Every bucket is forwarded: a cache read bills at ~0.1x input,
            # so pricing it as fresh input aborted well-cached runs early.
            charge_usage_to_budget(resolved_model, billed)

            # Book the cost on the tenant's cumulative ledger (enforced by
            # the pre-call gate above on the NEXT call; never raises).
            # Priced independently of the LoopBudget charge, which returns 0
            # outside an orchestrated request — background jobs meter too.
            await record_usage_cost(resolved_model, billed)

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
        except LLMRefusalError:
            # A model decision, not a failure: it keeps its class (callers
            # branch on it) and its warning-level log from the stop-reason
            # policy, instead of becoming a generic error logged at error.
            span.set_attribute("gen_ai.baselith.stop_reason", "refusal")
            raise
        except LLMProviderError as e:
            # Already neutral and typed (rate limit, 5xx, connection, client
            # error): re-wrapping it as "Generation failed" destroys the class
            # the retry layer and the caller decide on.
            span.set_attribute("gen_ai.baselith.error", str(e))
            raise
        except Exception as e:
            span.set_attribute("gen_ai.baselith.error", str(e))
            logger.error(f"Error generating response: {e}")
            raise LLMProviderError(f"Generation failed: {e}") from e


__all__ = ["generate_response"]
