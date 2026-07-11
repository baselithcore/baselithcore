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

from core.middleware.cost_control import (
    BudgetExceededError as MiddlewareBudgetExceededError,
)
from core.observability.logging import get_logger
from core.services.llm._deadline import stream_within_deadline
from core.services.llm._telemetry import (
    gen_ai_system,
    record_genai_metrics,
    report_tokens_to_middleware,
)
from core.services.llm.cost_control import estimate_tokens_async
from core.services.llm.exceptions import BudgetExceededError, LLMProviderError

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
        # Track input tokens (large prompts encode off the event loop)
        stream_input_tokens = await estimate_tokens_async(prompt)
        report_tokens_to_middleware(stream_input_tokens, model="input_stream")
        if service.cost_tracker:
            service.cost_tracker.track_tokens(stream_input_tokens, model="input_stream")

        try:
            accumulated_tokens = 0
            stream_started = time.perf_counter()
            stream_kwargs: dict = {}
            if system_prompt:
                stream_kwargs["system"] = system_prompt
            if temperature is not None:
                stream_kwargs["temperature"] = temperature
            if max_tokens is not None:
                stream_kwargs["max_tokens"] = max_tokens
            # Per-chunk deadline from the ambient LoopBudget: a stalled
            # stream cannot outlive the request's max_seconds.
            async for chunk, tokens in stream_within_deadline(
                service.provider.generate_stream(
                    prompt=prompt, model=model, **stream_kwargs
                )
            ):
                # Track incremental tokens
                new_tokens = tokens - accumulated_tokens
                if new_tokens > 0:
                    report_tokens_to_middleware(new_tokens, model=model)
                    if service.cost_tracker:
                        service.cost_tracker.track_tokens(new_tokens, model=model)
                accumulated_tokens = tokens

                yield chunk

            span.set_attribute("gen_ai.usage.output_tokens", accumulated_tokens)

            # Charge the completed stream against the ambient per-request
            # LoopBudget (no-op outside an orchestrated request). Charged
            # once at stream end so a mid-stream abort is never triggered
            # by the charge itself.
            from core.orchestration.budget_context import charge_llm_cost

            charge_llm_cost(
                model,
                stream_input_tokens,
                max(accumulated_tokens - stream_input_tokens, 0),
            )
            record_genai_metrics(
                gen_ai_system(service.config.provider),
                model,
                input_tokens=stream_input_tokens,
                output_tokens=max(accumulated_tokens - stream_input_tokens, 0),
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
