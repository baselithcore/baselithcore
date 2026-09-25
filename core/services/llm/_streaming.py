"""Streaming generation path for the LLM service.

Body of ``LLMService.generate_response_stream``, extracted (like
``structured.py``) to keep ``service.py`` under the module size cap. Same
accounting as the non-streaming path: token middleware reporting, ambient
LoopBudget charge at stream end, per-chunk deadline enforcement, and Gen AI
semconv metrics.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from core.lifecycle.deterministic import get_llm_override_kwargs
from core.middleware.cost_control import (
    BudgetExceededError as MiddlewareBudgetExceededError,
)
from core.models.pricing import qualified_model_id
from core.observability.logging import get_logger
from core.services.llm._accounting import (
    charge_usage_to_budget,
    record_usage_cost,
    set_usage_span_attributes,
)
from core.services.llm._stream_fallback import open_stream
from core.services.llm._telemetry import (
    gen_ai_system,
    record_genai_metrics,
    report_tokens_to_middleware,
)
from core.services.llm.cost_control import estimate_tokens_async
from core.services.llm.exceptions import BudgetExceededError, LLMProviderError
from core.services.llm.usage import Usage, billed_usage

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

logger = get_logger(__name__)


async def stream_response(
    service: LLMService,
    prompt: str,
    model: str | None = None,
    system_prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    """Stream a response, with full budget/telemetry accounting."""
    from core.observability import get_tracer

    # Lazy: a module-level import of core.orchestration would be circular
    # (orchestration handlers import the LLM service).
    from core.orchestration.limits import (
        BudgetExceededError as LoopBudgetExceededError,
    )

    model = service._resolve_model(model)
    tracer = get_tracer("llm-service")

    with tracer.start_span(
        f"chat {model}",
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.system": gen_ai_system(service.config.provider),
            "gen_ai.request.model": model,
            "gen_ai.baselith.prompt_length": len(prompt),
            "gen_ai.baselith.streaming": True,
        },
    ) as span:
        # Gate on the ambient tenant's cumulative USD budget before any
        # provider spend (no-op unless tenant cost limits are configured).
        from core.quotas.cost_enforcement import enforce_tenant_cost_budget

        await enforce_tenant_cost_budget(model=model)

        # Track input tokens (large prompts encode off the event loop)
        stream_input_tokens = await estimate_tokens_async(prompt)
        report_tokens_to_middleware(stream_input_tokens, model="input_stream")
        if service.cost_tracker:
            service.cost_tracker.track_tokens(stream_input_tokens, model="input_stream")

        try:
            # Providers yield a CUMULATIVE count that already includes the
            # prompt. Seeding the accumulator with the prompt estimate booked
            # above is what keeps it from being charged a second time on the
            # first chunk — the whole prompt used to land in that delta.
            accumulated_tokens = stream_input_tokens
            stream_started = time.perf_counter()
            stream_kwargs: dict = {}
            if system_prompt:
                stream_kwargs["system"] = system_prompt
            if temperature is not None:
                stream_kwargs["temperature"] = temperature
            if max_tokens is not None:
                stream_kwargs["max_tokens"] = max_tokens
            # CORE_DETERMINISTIC_MODE pins sampling on streams as well.
            stream_kwargs.update(get_llm_override_kwargs(service.config.provider))
            # Providers that read the stream's usage events publish the metered
            # record here (inert for the ones that ignore the kwarg). Without
            # it the output side is only ever ``cumulative - estimate(prompt)``
            # — an estimate subtracted from the provider's own figure.
            stream_usage: list[Usage] = []
            stream_kwargs["usage_sink"] = stream_usage
            # Opening the stream also applies the per-chunk deadline from the
            # ambient LoopBudget (a stalled stream cannot outlive the
            # request's max_seconds) and fails over to the configured
            # fallback chain while no chunk has reached the caller yet.
            # The concurrency guard (LLM_MAX_CONCURRENT_REQUESTS) is held for
            # the WHOLE stream: an open stream occupies the provider exactly
            # like a non-streaming call in flight.
            async with service._concurrency_guard():
                chunks, _serving, serving_provider, serving_model = await open_stream(
                    service, prompt, model, stream_kwargs
                )
                if serving_provider != service.config.provider:
                    span.set_attribute("gen_ai.baselith.served_by", serving_provider)
                    span.set_attribute("gen_ai.response.model", serving_model)
                    model = serving_model
                # Every ledger books the model that answered, namespaced by
                # provider when that provider is local: a bare local tag has no
                # pricing row, so it used to meter at UNKNOWN_PRICE.
                billing_model = qualified_model_id(serving_provider, model)
                async for chunk, tokens in chunks:
                    # Track incremental tokens. A provider's terminal usage
                    # event can correct the running estimate *downward*, which
                    # must never be reported as negative usage.
                    new_tokens = tokens - accumulated_tokens
                    if new_tokens > 0:
                        report_tokens_to_middleware(new_tokens, model=billing_model)
                        if service.cost_tracker:
                            service.cost_tracker.track_tokens(
                                new_tokens, model=billing_model
                            )
                    # The latest figure wins even when it corrects the
                    # running estimate downward: pricing and the span should
                    # carry what the provider billed, not the high-water mark.
                    accumulated_tokens = tokens

                    yield chunk

            # The provider's metered record when it reported one; the
            # estimate-based derivation otherwise.
            billed = billed_usage(
                stream_usage[-1] if stream_usage else None,
                fallback_input=stream_input_tokens,
                fallback_total=accumulated_tokens,
            )
            # Both halves of the semconv pair (plus the cache tiers): a span
            # carrying only the output count cannot be read as cost, or
            # compared against a sibling span.
            set_usage_span_attributes(span, billed)

            # Opt-in OpenInference enrichment on the same span. The streamed
            # completion text is not retained chunk-by-chunk, so content
            # capture covers the prompt side only here.
            from core.observability.openinference import openinference_llm_attributes

            for key, value in openinference_llm_attributes(
                model=billing_model,
                provider=serving_provider,
                # OpenInference has no cache tiers: its prompt count means
                # "tokens in the prompt", so it gets the whole prompt side.
                input_tokens=billed.prompt_tokens,
                output_tokens=billed.output_tokens,
                prompt=prompt,
            ).items():
                span.set_attribute(key, value)

            # Charge the completed stream against the ambient per-request
            # LoopBudget (no-op outside an orchestrated request). Charged
            # once at stream end so a mid-stream abort is never triggered
            # by the charge itself. Every bucket is forwarded: a cache read
            # bills at ~0.1x input, not at the full input rate.
            charge_usage_to_budget(billing_model, billed)

            # Book the stream's cost on the tenant's cumulative ledger
            # (enforced pre-call on the next generation; never raises).
            # Priced independently of the LoopBudget charge, which returns 0
            # outside an orchestrated request — background jobs meter too.
            await record_usage_cost(billing_model, billed)
            record_genai_metrics(
                gen_ai_system(serving_provider),
                billing_model,
                input_tokens=billed.input_tokens,
                output_tokens=billed.output_tokens,
                cache_read_tokens=billed.cache_read_tokens,
                cache_write_tokens=billed.cache_write_tokens,
                duration_seconds=time.perf_counter() - stream_started,
            )

        except (
            BudgetExceededError,
            MiddlewareBudgetExceededError,
            LoopBudgetExceededError,
        ):
            span.set_attribute("gen_ai.baselith.error", "budget_exceeded")
            raise
        except Exception as e:
            span.set_attribute("gen_ai.baselith.error", str(e))
            logger.error(f"Error in streaming generation: {e}")
            raise LLMProviderError(f"Streaming failed: {e}") from e


__all__ = ["stream_response"]
