"""Shared LLM telemetry helpers.

Extracted from ``service.py`` so both the legacy string path and the structured
tool-calling path (``structured.py``) emit identical ``gen_ai.*`` attributes and
forward token usage to the request-scoped cost controller the same way.
"""

from __future__ import annotations

from collections.abc import Callable

from core.middleware.cost_control import cost_controller
from core.observability.logging import get_logger

logger = get_logger(__name__)

# OTel GenAI semantic-convention `gen_ai.system` values for our providers
# (https://opentelemetry.io/docs/specs/semconv/gen-ai/). Falls back to the raw
# configured provider name lowercased for anything not mapped here.
_GEN_AI_SYSTEM = {
    "anthropic": "anthropic",
    "openai": "openai",
    "ollama": "ollama",
    "huggingface": "huggingface",
    # semconv value for the Gemini API is "gcp.gemini".
    "gemini": "gcp.gemini",
}


def gen_ai_system(provider: str | None) -> str:
    """Normalize the configured provider to a ``gen_ai.system`` value."""
    key = (provider or "").lower()
    return _GEN_AI_SYSTEM.get(key, key or "unknown")


# Observers for every token report, resolved at call time — so a consumer
# registered after this module is imported (e.g. a plugin installed at load
# time) still sees every subsequent report. Monkeypatching the module alias
# does NOT work for this: every call site imports the function directly.
TokenSink = Callable[[int, str], None]
_token_sinks: list[TokenSink] = []


def register_token_sink(sink: TokenSink) -> None:
    """Subscribe *sink* to every ``(count, model)`` token report. Idempotent.

    Sinks are best-effort observers (accounting, dashboards): exceptions they
    raise are swallowed, and they run even when the budget check raises — the
    tokens were consumed regardless.
    """
    if sink not in _token_sinks:
        _token_sinks.append(sink)


def unregister_token_sink(sink: TokenSink) -> None:
    """Remove a previously registered sink (no-op when absent)."""
    if sink in _token_sinks:
        _token_sinks.remove(sink)


def report_tokens_to_middleware(count: int, model: str) -> None:
    """Forward token usage to the request-scoped middleware cost controller.

    Propagates ``BudgetExceededError`` so ``CostControlMiddleware`` can translate
    it into a 429 response. Registered token sinks always run, even on that
    raise — consumed tokens must be accounted either way.
    """
    if count <= 0:
        return
    try:
        cost_controller.track_tokens(count, model=model)
    finally:
        for sink in list(_token_sinks):
            try:
                sink(count, model)
            except Exception:
                pass


#: Model ids the funnel uses to mean "these were prompt tokens" (paired with a
#: following real-model report so a consumer can reconstruct one call).
_INPUT_SENTINEL = "input"
_INPUT_STREAM_SENTINEL = "input_stream"


def report_external_usage(
    model: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    stream: bool = False,
) -> None:
    """Report token usage measured **outside** this funnel.

    For callers that do not go through ``core.services.llm`` at all — a
    vendored engine with its own provider client, an out-of-process child that
    returns its own counts — but whose usage must still reach every consumer of
    the report seam (per-plugin cost attribution, per-user accounting, the
    Gen AI metrics). Without it such an engine reports nothing and every
    consumer shows it as having spent zero.

    Emits the funnel's own **paired** reports in the funnel's own order: the
    prompt count under the ``input`` sentinel (``input_stream`` when *stream*),
    then the completion count under the real ``model`` id. A consumer pairs the
    two to reconstruct one call, so the order is load-bearing.

    Unlike :func:`report_tokens_to_middleware` this never raises: the tokens
    were already spent by an engine this process does not gate, so a budget
    rejection must neither corrupt a response that is already paid for nor
    swallow the second half of the pair.

    Args:
        model: The real model id the completion came from.
        prompt_tokens: Measured prompt/input tokens (skipped when <= 0).
        completion_tokens: Measured completion/output tokens (skipped when <= 0).
        stream: True when the completion was streamed, which selects the
            ``input_stream`` sentinel the streaming funnel path uses.
    """
    input_label = _INPUT_STREAM_SENTINEL if stream else _INPUT_SENTINEL
    for count, label in ((prompt_tokens, input_label), (completion_tokens, model)):
        if count <= 0:
            continue
        try:
            report_tokens_to_middleware(int(count), label)
        except Exception as exc:
            logger.debug(
                "external LLM usage report rejected",
                model=label,
                error=str(exc),
            )


def record_genai_metrics(
    system: str,
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_seconds: float | None = None,
    operation: str = "chat",
) -> None:
    """Emit OTel Gen AI semconv Prometheus metrics for one LLM call.

    Standard names (``gen_ai_client_token_usage``,
    ``gen_ai_client_operation_duration_seconds``) so semconv-aware dashboards
    light up without bespoke queries. Best-effort: metric registration/emit
    failures never break the request path.
    """
    try:
        from core.observability.metrics import (
            GEN_AI_OPERATION_DURATION,
            GEN_AI_TOKEN_USAGE,
        )

        if input_tokens > 0:
            GEN_AI_TOKEN_USAGE.labels(system, model, "input").observe(input_tokens)
        if output_tokens > 0:
            GEN_AI_TOKEN_USAGE.labels(system, model, "output").observe(output_tokens)
        if duration_seconds is not None:
            GEN_AI_OPERATION_DURATION.labels(system, model, operation).observe(
                duration_seconds
            )
        if input_tokens > 0 or output_tokens > 0:
            from core.models.pricing import DEFAULT_PRICING, estimate_cost
            from core.observability.metrics import GEN_AI_COST_USD

            if model in DEFAULT_PRICING:
                cost = estimate_cost(model, max(input_tokens, 0), max(output_tokens, 0))
                if cost > 0:
                    GEN_AI_COST_USD.labels(system, model).inc(cost)
    except Exception:  # pragma: no cover - metrics must never break requests
        pass


__all__ = [
    "gen_ai_system",
    "record_genai_metrics",
    "register_token_sink",
    "report_external_usage",
    "report_tokens_to_middleware",
    "unregister_token_sink",
]
