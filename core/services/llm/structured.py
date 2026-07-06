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
from typing import TYPE_CHECKING, Any

from core.observability import get_tracer
from core.observability.logging import get_logger
from core.resilience import retry
from core.services.llm._deadline import await_within_deadline
from core.services.llm._telemetry import gen_ai_system, report_tokens_to_middleware
from core.services.llm.cost_control import estimate_tokens
from core.services.llm.exceptions import LLMProviderError, RateLimitError
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolCall,
    ToolChoice,
)

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

logger = get_logger(__name__)


def _is_rate_limit(exc: Exception) -> bool:
    """Match the rate-limit heuristic used by the legacy retry layer."""
    error_str = str(exc).lower()
    return "429" in error_str or "rate limit" in error_str or "too many" in error_str


@retry(
    max_attempts=3,
    base_delay=1.0,
    max_delay=30.0,
    retryable_exceptions=(RateLimitError,),
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
    except Exception as e:
        if _is_rate_limit(e):
            logger.warning(f"Rate limit hit (structured), will retry: {e}")
            raise RateLimitError(str(e)) from e
        raise


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
) -> LLMResult:
    """Prompt-coercion path for providers without native tool calling."""
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

    content, tokens_used = await service._generate_with_retry(
        prompt=prompt, model=model, json_mode=want_json, **extra
    )
    result = _parse_fallback(content, has_tools=bool(tools))
    result.tokens_used = tokens_used
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

    Returns:
        LLMResult: text and/or structured tool calls with usage.
    """
    # Lazy: a module-level import of core.orchestration would be circular.
    from core.orchestration.budget_context import charge_llm_cost
    from core.orchestration.limits import (
        BudgetExceededError as LoopBudgetExceededError,
    )

    model = service._resolve_model(model)
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
        input_tokens = estimate_tokens(prompt)
        report_tokens_to_middleware(input_tokens, model="input")
        if service.cost_tracker:
            service.cost_tracker.track_tokens(input_tokens, model="input")

        try:
            if use_native:
                extra: dict[str, Any] = {}
                if system_prompt:
                    extra["system"] = system_prompt
                if temperature is not None:
                    extra["temperature"] = temperature
                if max_tokens is not None:
                    extra["max_tokens"] = max_tokens
                result = await _native_with_retry(
                    service,
                    prompt,
                    model,
                    tools=tools,
                    tool_choice=tool_choice,
                    response_format=response_format,
                    **extra,
                )
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
                )
        except (LoopBudgetExceededError, RateLimitError):
            span.set_attribute("gen_ai.baselith.error", "budget_or_rate_limit")
            raise
        except LLMProviderError:
            raise
        except Exception as e:
            span.set_attribute("gen_ai.baselith.error", str(e))
            logger.error(f"Structured generation failed: {e}")
            raise LLMProviderError(f"Structured generation failed: {e}") from e

        output_tokens = max(result.tokens_used - input_tokens, 0)
        span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
        span.set_attribute("gen_ai.baselith.tool_calls", len(result.tool_calls))
        if result.stop_reason:
            span.set_attribute("gen_ai.response.finish_reason", result.stop_reason)

        report_tokens_to_middleware(output_tokens, model=model)
        if service.cost_tracker:
            service.cost_tracker.track_tokens(output_tokens, model=model)

        # Charge real dollar cost against the ambient per-request LoopBudget
        # (no-op outside an orchestrated request).
        charge_llm_cost(model, input_tokens, output_tokens)

        return result


__all__ = ["generate_structured"]
