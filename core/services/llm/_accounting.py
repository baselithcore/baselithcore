"""Per-turn accounting shared by the generation paths.

One completed turn has to reach four places: the middleware cost controller,
the optional per-service cost tracker, the Gen AI metrics histograms and the
ambient request budget — plus the span, in OTel's semantic conventions, and
the tenant's cumulative ledger. Having that sequence written out once means a
refusal (which is generated, billed output) is booked exactly like an answer
instead of quietly escaping every ledger on its way out.

Every helper here takes a whole :class:`~core.services.llm.usage.Usage`
record rather than an ``(input, output)`` pair. That is deliberate: the pair
folded ``cache_read_tokens`` and ``cache_write_tokens`` into the input figure,
and each of the eleven call sites then had to remember to re-split it — none
did, so cached prompt tokens (~0.1x input) were priced at the full input rate
in every ledger. A record forwards its buckets by name and cannot be
mis-attributed.

Split from ``structured`` for the module size cap.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.services.llm._telemetry import (
    gen_ai_system,
    record_genai_metrics,
    report_tokens_to_middleware,
)
from core.services.llm.tool_calling import LLMResult
from core.services.llm.usage import Usage, billed_usage

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

logger = get_logger(__name__)

__all__ = [
    "account_turn",
    "charge_usage_to_budget",
    "record_usage_cost",
    "set_usage_span_attributes",
]


def set_usage_span_attributes(span: Any, usage: Usage) -> None:
    """Write one turn's token buckets onto *span* in OTel semconv terms.

    ``gen_ai.usage.input_tokens`` carries *fresh* input only, exactly as the
    providers report it (Anthropic's ``input_tokens`` excludes both cache
    counters and the OpenAI reader subtracts its cached prefix out). Cached
    prompt tokens get their own attributes because they are their own price
    tier; folding them into the input count made a well-cached call look five
    times more expensive than it was.

    Args:
        span: The active generation span.
        usage: The billed record for the turn.
    """
    span.set_attribute("gen_ai.usage.input_tokens", usage.input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", usage.output_tokens)
    if usage.cache_read_tokens:
        span.set_attribute("gen_ai.usage.cache_read_tokens", usage.cache_read_tokens)
    if usage.cache_write_tokens:
        span.set_attribute("gen_ai.usage.cache_write_tokens", usage.cache_write_tokens)


def charge_usage_to_budget(model: str, usage: Usage) -> float:
    """Charge one turn against the ambient per-request ``LoopBudget``.

    All four buckets are forwarded, so a cache read is priced at its own rate
    (~0.1x input) instead of at the full input rate — which is what made
    ``LoopLimits.budget_usd`` abort well-cached runs roughly five times too
    early.

    Args:
        model: The model that served the turn.
        usage: The billed record for the turn.

    Returns:
        float: USD charged (0.0 outside an orchestrated request).

    Raises:
        core.orchestration.limits.BudgetExceededError: When the charge pushes
            the request over its ``budget_usd`` cap.
    """
    from core.orchestration.budget_context import charge_llm_cost

    return charge_llm_cost(
        model,
        usage.input_tokens,
        usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
    )


async def record_usage_cost(model: str, usage: Usage, *, batch: bool = False) -> None:
    """Book one turn's dollar cost on the ambient tenant's cumulative ledger.

    The one ledger :func:`account_turn` cannot book itself (it is sync and
    this is async), and the one every path has to remember — it is what
    :func:`core.quotas.cost_enforcement.enforce_tenant_cost_budget` gates the
    *next* call on. Priced independently of the ``LoopBudget`` charge, which
    returns 0 outside an orchestrated request: background jobs meter too.

    Never raises. In particular an unpriced model under
    ``BASELITH_UNKNOWN_MODEL_COST_POLICY=reject`` is a "don't meter", not a
    run-aborting error: this runs *after* the provider has answered and been
    paid, so letting ``UnknownModelCostRejected`` out here would destroy a
    completed generation the user already owes money for. The reject policy's
    documented meaning ("raises instead of billing anything") is implemented
    where it can hold — the pre-call gate in ``enforce_tenant_cost_budget``.

    Args:
        model: The model that served the turn.
        usage: The billed record for the turn.
        batch: True when the turn was served by a batch API, which bills at
            half the interactive rate across every bucket. Only the batch
            path may set it: an interactive call priced as a batch would
            under-meter by 2x, exactly as a batch priced interactively
            over-meters by 2x.
    """
    from core.quotas.cost_enforcement import (
        UnknownModelCostRejected,
        llm_call_cost_usd,
        record_tenant_llm_cost,
    )

    try:
        cost = llm_call_cost_usd(
            model,
            usage.input_tokens,
            usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            batch=batch,
        )
    except UnknownModelCostRejected:
        # ``price_unknown_model`` already logged this model once per process.
        logger.debug("llm_cost_not_metered_unknown_model", extra={"model": model})
        return
    await record_tenant_llm_cost(cost)


def account_turn(
    service: LLMService,
    span: Any,
    *,
    model: str,
    result: LLMResult,
    input_tokens: int,
    started: float,
) -> Usage:
    """Book one completed turn against every per-request ledger and the span.

    Shared by the success path and the refusal path: a refusal is generated,
    billed output, so it is accounted for exactly like an answer before the
    error propagates.

    Args:
        service: The owning :class:`LLMService`.
        span: The active generation span.
        model: The model that served the turn.
        result: What came back (or what is known of it, for a refusal).
        input_tokens: The prompt estimate already booked pre-call.
        started: ``time.perf_counter()`` at the start of the call.

    Returns:
        Usage: The billed four-bucket record, for the caller's remaining
        ledger — the tenant's cumulative cost, via :func:`record_usage_cost`.
    """
    # The provider's metered record when it reported one; the legacy
    # "total minus an estimate of the prompt" derivation otherwise.
    billed = billed_usage(
        result.usage,
        fallback_input=input_tokens,
        fallback_total=result.tokens_used,
    )
    # The middleware ledger already booked the estimated prompt side pre-call,
    # so only the remainder of the total may be added — the exact split drives
    # pricing and telemetry, not the running total.
    output_tokens = max(result.tokens_used - input_tokens, 0)
    set_usage_span_attributes(span, billed)
    span.set_attribute("gen_ai.baselith.tool_calls", len(result.tool_calls))
    if isinstance(result.stop_reason, str) and result.stop_reason:
        span.set_attribute("gen_ai.response.finish_reason", result.stop_reason)

    report_tokens_to_middleware(output_tokens, model=model)
    if service.cost_tracker:
        service.cost_tracker.track_tokens(output_tokens, model=model)
    record_genai_metrics(
        gen_ai_system(service.config.provider),
        model,
        input_tokens=billed.input_tokens,
        output_tokens=billed.output_tokens,
        cache_read_tokens=billed.cache_read_tokens,
        cache_write_tokens=billed.cache_write_tokens,
        duration_seconds=time.perf_counter() - started,
    )
    # Charge real dollar cost against the ambient per-request LoopBudget
    # (no-op outside an orchestrated request).
    charge_usage_to_budget(model, billed)
    return billed
