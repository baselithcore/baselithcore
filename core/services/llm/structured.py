"""Structured / native tool-calling orchestration for the LLM service.

The legacy path is ``prompt: str -> str``. This module adds the structured path
(``LLMService.generate -> LLMResult``): a request may carry tool specs, a
tool-choice policy, and an optional response-format constraint, and the result
carries parsed tool calls alongside any text.

Two execution modes, chosen per request:

* **Native** — when native tools are enabled (``LLMConfig.enable_native_tools``)
  *and* the active provider advertises ``supports_native_tools``. Delegates to
  the provider's ``generate_structured`` and returns provider-parsed tool calls.
* **Fallback** — otherwise. Describes the tools (and any response schema) in an
  augmented system prompt, requests JSON via the legacy string path, and parses
  a ``{"tool": ..., "arguments": {...}}`` object back into a :class:`ToolCall`.

Kept out of ``service.py`` to respect the module size cap and so both modes
share the same span / token / budget accounting.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from core.lifecycle.deterministic import get_llm_override_kwargs
from core.models.pricing import qualified_model_id
from core.observability import get_tracer
from core.observability.logging import get_logger
from core.resilience import retry
from core.services.llm._accounting import account_turn, record_usage_cost
from core.services.llm._deadline import await_within_deadline
from core.services.llm._telemetry import (
    gen_ai_system,
    report_tokens_to_middleware,
)
from core.services.llm.cost_control import estimate_tokens_async
from core.services.llm.errors import (
    RETRYABLE_ERRORS,
    LLMRefusalError,
    is_retryable,
    retry_after_from_exception,
)
from core.services.llm.exceptions import LLMProviderError, RateLimitError
from core.services.llm.stop_reasons import STOP_REFUSAL, apply_stop_reason
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolCall,
    ToolChoice,
)
from core.services.llm.usage import Usage

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

logger = get_logger(__name__)


#: Same eligibility as the text path: transient classes retry, client errors
#: and refusals do not.
_RETRYABLE = RETRYABLE_ERRORS


def _is_rate_limit(exc: Exception) -> bool:
    """Whether *exc* is worth retrying.

    Kept under its historical name for callers that import it; the decision
    now comes from the neutral error taxonomy, with the substring heuristic
    only as the fallback for unmapped exception types.
    """
    return is_retryable(exc)


@retry(
    max_attempts=3,
    base_delay=1.0,
    max_delay=30.0,
    retryable_exceptions=_RETRYABLE,
)
async def _native_with_retry(
    service: LLMService,
    prompt: str,
    model: str,
    *,
    tools: list[LLMToolSpec] | None,
    tool_choice: ToolChoice | None,
    response_format: ResponseFormat | None,
    **kwargs: Any,
) -> LLMResult:
    """Call the provider's native structured API with rate-limit retry.

    Mirrors ``LLMService._generate_with_retry``: only rate-limit errors retry;
    everything else fails fast and feeds the provider circuit breaker.
    """
    try:
        # Bounded by the ambient LoopBudget's remaining wall-clock time
        # (plain await outside an orchestrated request).
        return await await_within_deadline(
            service.provider.generate_structured(
                prompt,
                model,
                tools=tools,
                tool_choice=tool_choice,
                response_format=response_format,
                **kwargs,
            )
        )
    except _RETRYABLE as e:
        # Already typed by the provider's error mapping.
        logger.warning(
            "Transient provider failure (structured, %s), will retry: %s",
            type(e).__name__,
            e,
        )
        raise
    except Exception as e:
        if not is_retryable(e):
            raise
        logger.warning(f"Rate limit hit (structured), will retry: {e}")
        raise RateLimitError(str(e), retry_after=retry_after_from_exception(e)) from e


def _render_tools(tools: list[LLMToolSpec]) -> str:
    """Render tool specs as a compact JSON catalog for the fallback prompt."""
    catalog = [
        {"name": t.name, "description": t.description, "parameters": t.parameters}
        for t in tools
    ]
    return json.dumps(catalog, ensure_ascii=False, sort_keys=True)


def _build_fallback_system(
    base_system: str | None,
    tools: list[LLMToolSpec] | None,
    tool_choice: ToolChoice | None,
    response_format: ResponseFormat | None,
) -> str:
    """Augment the system prompt to coerce tool calls / structured JSON.

    Used for providers without a native tool API. Deterministic (sorted keys)
    so it doesn't defeat prompt caching.
    """
    parts: list[str] = []
    if base_system:
        parts.append(base_system)

    if tools:
        choice = tool_choice or ToolChoice(mode="auto")
        parts.append("You can call tools. Available tools (JSON):")
        parts.append(_render_tools(tools))
        if choice.mode == "tool":
            parts.append(
                f'You MUST call the tool "{choice.name}". Respond with ONLY a '
                'JSON object: {"tool": "' + str(choice.name) + '", "arguments": {...}}.'
            )
        elif choice.mode == "any":
            parts.append(
                "You MUST call one tool. Respond with ONLY a JSON object: "
                '{"tool": <tool name>, "arguments": {...}}.'
            )
        else:
            parts.append(
                "To call a tool, respond with ONLY a JSON object: "
                '{"tool": <tool name>, "arguments": {...}}. '
                'If no tool is needed, respond with {"tool": null, '
                '"final": <your answer as a string>}.'
            )
    elif response_format is not None:
        parts.append(
            "Respond with ONLY a JSON object that conforms to this JSON Schema (JSON):"
        )
        parts.append(
            json.dumps(response_format.schema, ensure_ascii=False, sort_keys=True)
        )

    return "\n\n".join(parts)


def _parse_fallback(content: str, has_tools: bool) -> LLMResult:
    """Parse a fallback JSON response into an :class:`LLMResult`.

    Tolerant: on malformed JSON (or JSON that isn't a tool-call object) the raw
    text is returned as ``text`` with no tool calls, so the caller degrades to a
    plain answer rather than erroring.
    """
    if not has_tools:
        # response_format-only (or plain) path: the JSON *is* the answer.
        return LLMResult(text=content or None, native=False)

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return LLMResult(text=content or None, native=False)

    if not isinstance(parsed, dict):
        return LLMResult(text=content or None, native=False)

    tool_name = parsed.get("tool")
    if tool_name:
        arguments = parsed.get("arguments")
        return LLMResult(
            text=None,
            tool_calls=[
                ToolCall(
                    id="fallback-call-0",
                    name=str(tool_name),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            ],
            stop_reason="tool_use",
            native=False,
        )

    # Explicit no-tool answer.
    final = parsed.get("final")
    return LLMResult(text=str(final) if final is not None else content, native=False)


async def _generate_fallback(
    service: LLMService,
    prompt: str,
    model: str,
    *,
    tools: list[LLMToolSpec] | None,
    tool_choice: ToolChoice | None,
    response_format: ResponseFormat | None,
    system_prompt: str | None,
    temperature: float | None,
    max_tokens: int | None,
    allow_refusal: bool = False,
    usage_sink: list[Usage] | None = None,
) -> LLMResult:
    """Prompt-coercion path for providers without native tool calling.

    ``allow_refusal`` is forwarded as a provider kwarg rather than applied
    here: this path returns through the legacy text API, where the provider
    itself decides whether a refusal raises (it has the stop reason; the
    ``(text, tokens)`` return type does not carry one back).
    """
    augmented_system = _build_fallback_system(
        system_prompt, tools, tool_choice, response_format
    )
    want_json = bool(tools) or response_format is not None

    extra: dict[str, Any] = {}
    if augmented_system:
        extra["system"] = augmented_system
    if temperature is not None:
        extra["temperature"] = temperature
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens
    if allow_refusal:
        extra["allow_refusal"] = True

    # Owned by the caller when it passed one: a refusal raises out of this
    # function, and the turn still has to be accounted for.
    usage_sink = [] if usage_sink is None else usage_sink
    content, tokens_used = await service._generate_with_retry(
        prompt=prompt,
        model=model,
        json_mode=want_json,
        usage_sink=usage_sink,
        **extra,
    )
    result = _parse_fallback(content, has_tools=bool(tools))
    result.tokens_used = tokens_used
    if usage_sink:
        result.usage = usage_sink[-1]
    return result


async def generate_structured(
    service: LLMService,
    prompt: str,
    *,
    model: str | None = None,
    tools: list[LLMToolSpec] | None = None,
    tool_choice: ToolChoice | None = None,
    response_format: ResponseFormat | None = None,
    system_prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    task_category: str | None = None,
    allow_refusal: bool = False,
) -> LLMResult:
    """Generate a structured response (tool calls and/or text).

    Routes to the provider's native tool API when enabled and supported,
    otherwise to the prompt-coercion fallback. Emits a ``gen_ai.*`` span and
    charges token usage against the middleware cost controller, the optional
    cost tracker, and the ambient per-request LoopBudget — identical accounting
    to the legacy string path.

    Args:
        service: The owning :class:`LLMService`.
        prompt: User turn.
        model: Optional model override (config default when None).
        tools: Tools the model may call.
        tool_choice: Selection policy (defaults to auto when tools present).
        response_format: Optional structured-output constraint.
        system_prompt: Optional system prompt.
        temperature: Optional sampling temperature.
        max_tokens: Optional output token cap.
        task_category: Optional task category hint for cost-aware routing
            (ignored unless routing is enabled).
        allow_refusal: When True a model refusal is returned on the result
            instead of raising ``LLMRefusalError``.

    Returns:
        LLMResult: text and/or structured tool calls with usage.

    Raises:
        LLMRefusalError: The model declined to answer and ``allow_refusal``
            is False.
    """
    # Lazy: a module-level import of core.orchestration would be circular.
    from core.orchestration.limits import (
        BudgetExceededError as LoopBudgetExceededError,
    )
    from core.quotas.cost_enforcement import enforce_tenant_cost_budget
    from core.quotas.manager import CostBudgetExceededError

    model = service._resolve_model(model, task_category)
    native_enabled = bool(getattr(service.config, "enable_native_tools", False))
    use_native = native_enabled and bool(
        getattr(service.provider, "supports_native_tools", False)
    )

    tracer = get_tracer("llm-service")
    span_attributes: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.system": gen_ai_system(service.config.provider),
        "gen_ai.request.model": model,
        "gen_ai.baselith.native_tools": use_native,
        "gen_ai.baselith.tool_count": len(tools) if tools else 0,
        "gen_ai.baselith.structured": response_format is not None,
    }

    with tracer.start_span(f"chat {model}", attributes=span_attributes) as span:
        # Gate on the ambient tenant's cumulative USD budget BEFORE any
        # provider spend (no-op unless tenant cost limits are configured;
        # fails open on store errors). The text and streaming paths have
        # always done this; native tool calling did not, so a deployment
        # whose traffic is agentic wrote a ledger nothing ever read and the
        # cap could not trip.
        try:
            await enforce_tenant_cost_budget(model=model)
        except CostBudgetExceededError:
            span.set_attribute("gen_ai.baselith.error", "tenant_cost_budget_exceeded")
            raise

        input_tokens = await estimate_tokens_async(prompt)
        report_tokens_to_middleware(input_tokens, model="input")
        if service.cost_tracker:
            service.cost_tracker.track_tokens(input_tokens, model="input")

        import time

        started = time.perf_counter()
        # Held by this frame so a refusal raised out of the coercion path
        # still carries the provider's metered usage to the accounting below.
        fallback_usage: list[Usage] = []
        # Who ends up answering. Overwritten by the native path when a fallback
        # stage serves; the coercion path below never fails over, so the
        # configured provider is already the truth for it.
        serving_provider = service.config.provider
        serving_model = model
        try:
            if use_native:
                extra: dict[str, Any] = {}
                if system_prompt:
                    extra["system"] = system_prompt
                if temperature is not None:
                    extra["temperature"] = temperature
                if max_tokens is not None:
                    extra["max_tokens"] = max_tokens
                # CORE_DETERMINISTIC_MODE pins sampling here as well (the
                # coercion branch gets it from _generate_with_retry).
                extra.update(get_llm_override_kwargs(service.config.provider))
                # Cross-provider resilience for the primary structured path:
                # with LLM_FALLBACK_CHAIN configured, provider failures fall
                # through to native-capable fallback stages (open breakers
                # and budget/deadline errors never fall through).
                from core.services.llm.fallback_runtime import (
                    maybe_run_structured_with_fallback,
                )

                (
                    native_result,
                    serving_provider,
                    serving_model,
                ) = await maybe_run_structured_with_fallback(
                    service,
                    prompt,
                    model,
                    tools=tools,
                    tool_choice=tool_choice,
                    response_format=response_format,
                    **extra,
                )
                result = cast("LLMResult", native_result)
                span.set_attribute("gen_ai.baselith.serving_provider", serving_provider)
                span.set_attribute("gen_ai.response.model", serving_model)
            else:
                result = await _generate_fallback(
                    service,
                    prompt,
                    model,
                    tools=tools,
                    tool_choice=tool_choice,
                    response_format=response_format,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    allow_refusal=allow_refusal,
                    usage_sink=fallback_usage,
                )
        except LLMRefusalError:
            # A refusal is generated output: the provider ran the model and
            # billed for it. Account for the turn before the error propagates,
            # or the spend disappears from the middleware ledger, the metrics
            # and the request budget. (The native path never lands here — its
            # refusal is raised by ``apply_stop_reason`` *after* accounting.)
            billing_model = qualified_model_id(serving_provider, serving_model)
            billed = account_turn(
                service,
                span,
                model=billing_model,
                result=LLMResult(
                    stop_reason=STOP_REFUSAL,
                    usage=fallback_usage[-1] if fallback_usage else Usage(),
                    native=use_native,
                ),
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
            logger.error(f"Structured generation failed: {e}")
            raise LLMProviderError(f"Structured generation failed: {e}") from e

        # The model that answered, provider-namespaced when local: see
        # ``core.models.pricing.qualified_model_id``.
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
        # Book the cost on the tenant's cumulative ledger, exactly as the text
        # and streaming paths do. ``account_turn`` deliberately stops at the
        # per-request ledgers (middleware, metrics, LoopBudget) and hands back
        # the billed split for this; structured generation never took it, so a
        # deployment whose agents use tool calling metered a fraction of its
        # real spend and the tenant cost cap never tripped. Priced
        # independently of the LoopBudget charge, which returns 0 outside an
        # orchestrated request — background jobs meter too. Never raises.
        await record_usage_cost(billing_model, billed)

        # Stop-reason policy last: the call is accounted for either way (it was
        # billed), and only then does a refusal abort the caller.
        return apply_stop_reason(result, model=model, allow_refusal=allow_refusal)


# ``generate_typed`` (the Pydantic bridge) lives in ``typed`` for the
# module size cap; re-exported here for the historical import path.
from core.services.llm.typed import generate_typed  # noqa: E402

__all__ = ["generate_structured", "generate_typed"]
