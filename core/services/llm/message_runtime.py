"""Service-level message API: one turn of a real conversation.

:func:`core.services.llm.structured.generate_structured` takes a *prompt*. An
agentic loop does not have one — it has a history, and flattening that history
into a prompt on every iteration is precisely the defect this path exists to
remove (tool-call correlation, ``is_error`` and thinking blocks are lost, and
the rebuilt prefix defeats the prompt cache).

Two modes, chosen per request:

* **Native messages** — the provider advertises ``supports_messages`` and
  native tools are enabled. The history goes to ``provider.generate_messages``
  as-is, and the turn is accounted for exactly like a structured one.
* **Degraded** — anything else. The history is rendered as a transcript by
  :func:`core.services.llm.messages.render_as_prompt` and handed to the
  existing structured path, which brings its own span, retry and accounting.
  The conversation still reaches the model; only the structure is lost.

Kept out of ``service.py`` (module size cap), like ``structured`` and
``_streaming``; ``LLMService.generate_messages`` is the public entry point.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast

from core.models.pricing import qualified_model_id
from core.observability import get_tracer
from core.observability.logging import get_logger
from core.resilience import retry
from core.services.llm._accounting import account_turn, record_usage_cost
from core.services.llm._deadline import await_within_deadline
from core.services.llm._telemetry import gen_ai_system, report_tokens_to_middleware
from core.services.llm.cost_control import estimate_tokens_async
from core.services.llm.errors import (
    RETRYABLE_ERRORS,
    LLMRefusalError,
    is_retryable,
    retry_after_from_exception,
)
from core.services.llm.exceptions import LLMProviderError, RateLimitError
from core.services.llm.messages import (
    CONVERGENCE_NUDGE,
    Message,
    ToolResultBlock,
    render_as_prompt,
)
from core.services.llm.stop_reasons import STOP_REFUSAL, apply_stop_reason
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolChoice,
)
from core.services.llm.usage import Usage

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.services.llm.interfaces import MessageCapableProvider
    from core.services.llm.service import LLMService

logger = get_logger(__name__)

__all__ = ["generate_messages", "supports_message_api"]

#: Same eligibility as every other path: transient classes retry, client
#: errors and refusals do not.
_RETRYABLE = RETRYABLE_ERRORS


def supports_message_api(service: LLMService) -> bool:
    """Whether *service* will send a real message list for this configuration.

    Both halves matter: a provider that cannot receive messages obviously
    degrades, but so does one whose native tool API is switched off — the
    coercion fallback speaks prompts, and pretending otherwise would send a
    tool-calling conversation to a path that cannot answer one.
    """
    if not getattr(service.config, "enable_native_tools", False):
        return False
    return bool(getattr(service.provider, "supports_messages", False))


@retry(
    max_attempts=3,
    base_delay=1.0,
    max_delay=30.0,
    retryable_exceptions=_RETRYABLE,
)
async def _messages_with_retry(
    service: LLMService,
    messages: list[Message],
    model: str,
    **kwargs: Any,
) -> LLMResult:
    """Call the provider's message API with rate-limit retry.

    Mirrors ``structured._native_with_retry``: only transient failures retry;
    everything else fails fast and feeds the provider circuit breaker.
    """
    # Narrowed by ``supports_message_api`` at the only call site: the message
    # API is an optional capability (:class:`MessageCapableProvider`), so the
    # base provider protocol does not declare it.
    provider = cast("MessageCapableProvider", service.provider)
    try:
        # Bounded by the ambient LoopBudget's remaining wall-clock time.
        return await await_within_deadline(
            provider.generate_messages(messages, model, **kwargs)
        )
    except _RETRYABLE as e:
        logger.warning(
            "Transient provider failure (messages, %s), will retry: %s",
            type(e).__name__,
            e,
        )
        raise
    except Exception as e:
        if not is_retryable(e):
            raise
        logger.warning(f"Rate limit hit (messages), will retry: {e}")
        raise RateLimitError(str(e), retry_after=retry_after_from_exception(e)) from e


async def _degrade_to_prompt(
    service: LLMService,
    messages: list[Message],
    *,
    model: str,
    tools: list[LLMToolSpec] | None,
    tool_choice: ToolChoice | None,
    response_format: ResponseFormat | None,
    system: str | None,
    temperature: float | None,
    max_tokens: int | None,
    allow_refusal: bool,
) -> LLMResult:
    """Send the conversation as a transcript through the structured path.

    The transcript carries a convergence nudge that the structured message
    shape does not need: a provider reading a flattened conversation has no
    ``tool_result`` block to tell it the work came back, and without the
    instruction it re-requests tool calls it has already been answered until
    the iteration cap. It is appended *here* rather than inside
    ``render_as_prompt``, which is also used to estimate input tokens — an
    instruction is not part of the conversation being measured.
    """
    from core.services.llm.structured import generate_structured

    logger.debug(
        "llm_messages_degraded_to_prompt",
        extra={"model": model, "provider": service.config.provider},
    )
    transcript = render_as_prompt(messages)
    if any(isinstance(block, ToolResultBlock) for m in messages for block in m.content):
        transcript = f"{transcript}\n\n{CONVERGENCE_NUDGE}"
    return await generate_structured(
        service,
        transcript,
        model=model,
        tools=tools,
        tool_choice=tool_choice,
        response_format=response_format,
        system_prompt=system,
        temperature=temperature,
        max_tokens=max_tokens,
        allow_refusal=allow_refusal,
    )


async def generate_messages(
    service: LLMService,
    messages: list[Message],
    *,
    model: str | None = None,
    tools: list[LLMToolSpec] | None = None,
    tool_choice: ToolChoice | None = None,
    response_format: ResponseFormat | None = None,
    system: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    task_category: str | None = None,
    allow_refusal: bool = False,
) -> LLMResult:
    """Generate one turn from a neutral conversation history.

    See :meth:`core.services.llm.service.LLMService.generate_messages` for the
    argument contract; this is its implementation.

    Args:
        service: The owning :class:`LLMService`.
        messages: Conversation so far, oldest first.
        model: Optional model override (config default when None).
        tools: Tools the model may call.
        tool_choice: Selection policy (defaults to auto when tools present).
        response_format: Optional structured-output constraint.
        system: System prompt — the stable, cacheable prefix.
        temperature: Optional sampling temperature.
        max_tokens: Optional output token cap.
        task_category: Optional cost-aware routing hint.
        allow_refusal: When True a refusal is returned on the result instead
            of raising ``LLMRefusalError``.

    Returns:
        LLMResult: text and/or structured tool calls with usage, and
        ``message`` when the provider built the assistant turn.

    Raises:
        LLMRefusalError: The model declined and ``allow_refusal`` is False.
    """
    from core.orchestration.limits import (
        BudgetExceededError as LoopBudgetExceededError,
    )
    from core.quotas.cost_enforcement import enforce_tenant_cost_budget
    from core.quotas.manager import CostBudgetExceededError

    resolved_model = service._resolve_model(model, task_category)
    if not supports_message_api(service):
        return await _degrade_to_prompt(
            service,
            messages,
            model=resolved_model,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            allow_refusal=allow_refusal,
        )

    tracer = get_tracer("llm-service")
    span_attributes: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.system": gen_ai_system(service.config.provider),
        "gen_ai.request.model": resolved_model,
        "gen_ai.baselith.native_tools": True,
        "gen_ai.baselith.message_api": True,
        "gen_ai.baselith.tool_count": len(tools) if tools else 0,
        "gen_ai.baselith.turns": len(messages),
        "gen_ai.baselith.structured": response_format is not None,
    }

    with tracer.start_span(
        f"chat {resolved_model}", attributes=span_attributes
    ) as span:
        # Gate on the ambient tenant's cumulative USD budget BEFORE any
        # provider spend (no-op unless tenant cost limits are configured;
        # fails open on store errors). The agent loop runs through here, so
        # without this gate the tenant cap was written by four paths and
        # enforced on two — an agentic deployment could never trip it.
        try:
            await enforce_tenant_cost_budget(model=resolved_model)
        except CostBudgetExceededError:
            span.set_attribute("gen_ai.baselith.error", "tenant_cost_budget_exceeded")
            raise

        # Estimated from the transcript: the pre-call middleware ledger only
        # needs a size, and the provider's metered split replaces it below.
        input_tokens = await estimate_tokens_async(render_as_prompt(messages))
        report_tokens_to_middleware(input_tokens, model="input")
        if service.cost_tracker:
            service.cost_tracker.track_tokens(input_tokens, model="input")

        extra: dict[str, Any] = {}
        if temperature is not None:
            extra["temperature"] = temperature
        if max_tokens is not None:
            extra["max_tokens"] = max_tokens
        if allow_refusal:
            extra["allow_refusal"] = True

        started = time.perf_counter()
        # Overwritten below by whichever stage answers; pre-seeded so the
        # refusal handler can attribute the turn without a NameError.
        serving_provider = service.config.provider
        serving_model = resolved_model
        try:
            # Through the fallback chain, never straight at the provider: with
            # LLM_FALLBACK_CHAIN configured, a provider failure falls through
            # to the next message-capable stage (open breakers are skipped and
            # budget/deadline errors stay fatal). Calling the provider directly
            # here would have silently dropped cross-provider failover the
            # moment an agent took the message path.
            from core.services.llm.fallback_runtime import (
                maybe_run_messages_with_fallback,
            )

            (
                raw_result,
                serving_provider,
                serving_model,
            ) = await maybe_run_messages_with_fallback(
                service,
                messages,
                resolved_model,
                tools=tools,
                system=system,
                tool_choice=tool_choice,
                response_format=response_format,
                **extra,
            )
            result = cast("LLMResult", raw_result)
            span.set_attribute("gen_ai.baselith.serving_provider", serving_provider)
            span.set_attribute("gen_ai.response.model", serving_model)
        except LLMRefusalError:
            # A refusal is generated, billed output: book the turn before the
            # error propagates, or the spend leaves no trace in any ledger.
            billing_model = qualified_model_id(serving_provider, serving_model)
            billed = account_turn(
                service,
                span,
                model=billing_model,
                result=LLMResult(stop_reason=STOP_REFUSAL, usage=Usage()),
                input_tokens=input_tokens,
                started=started,
                provider=serving_provider,
            )
            await record_usage_cost(billing_model, billed)
            raise
        except (LoopBudgetExceededError, RateLimitError):
            span.set_attribute("gen_ai.baselith.error", "budget_or_rate_limit")
            raise
        except LLMProviderError:
            raise
        except Exception as e:
            span.set_attribute("gen_ai.baselith.error", str(e))
            logger.error(f"Message generation failed: {e}")
            raise LLMProviderError(f"Message generation failed: {e}") from e

        # The model that answered, provider-namespaced when local.
        billing_model = qualified_model_id(serving_provider, serving_model)
        billed = account_turn(
            service,
            span,
            model=billing_model,
            result=result,
            input_tokens=input_tokens,
            started=started,
            provider=serving_provider,
        )
        # Book the cost on the tenant's cumulative ledger, exactly as the text,
        # streaming and structured paths do. ``account_turn`` deliberately
        # stops at the per-request ledgers (middleware, metrics, LoopBudget)
        # and hands back the billed split for this. Missing it here would make
        # the flagship agent loop invisible to the tenant's cumulative spend:
        # unmetered cost, and a cost cap that silently never fires. Priced
        # independently of the LoopBudget charge, which returns 0 outside an
        # orchestrated request — background jobs meter too. Never raises.
        #
        # Only the native branch books here: the degraded branch returns
        # through ``generate_structured``, which books it already, so booking
        # again would double-charge the tenant.
        await record_usage_cost(billing_model, billed)
        # Stop-reason policy last: the call is accounted for either way.
        return apply_stop_reason(
            result, model=resolved_model, allow_refusal=allow_refusal
        )
