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

# OTel GenAI semantic-convention ``gen_ai.provider.name`` values for our
# providers (well-known values from
# https://github.com/open-telemetry/semantic-conventions-genai, registry
# ``gen-ai.md``). Anything not mapped falls back to the configured provider
# name lowercased, which the spec allows as a custom value.
_GEN_AI_PROVIDER_NAME = {
    "anthropic": "anthropic",
    "openai": "openai",
    "azure_openai": "azure.ai.openai",
    "bedrock": "aws.bedrock",
    "vertex": "gcp.vertex_ai",
    "vertex_ai": "gcp.vertex_ai",
    # The Gemini API (generativelanguage.googleapis.com) is "gcp.gemini".
    "gemini": "gcp.gemini",
    "mistral": "mistral_ai",
    "groq": "groq",
    "deepseek": "deepseek",
    # No well-known value; the lowercase product name is the custom value.
    "ollama": "ollama",
    "huggingface": "huggingface",
    "vllm": "vllm",
}


def gen_ai_provider_name(provider: str | None) -> str:
    """Normalize the configured provider to a ``gen_ai.provider.name`` value."""
    key = (provider or "").lower()
    return _GEN_AI_PROVIDER_NAME.get(key, key or "unknown")


# The Anthropic SDK can serve Claude through a cloud's own endpoint; the spec
# names the provider by the endpoint actually reached, not the model vendor.
_ANTHROPIC_BACKEND_PROVIDER = {
    "bedrock": "aws.bedrock",
    "vertex": "gcp.vertex_ai",
}


def gen_ai_provider_for(config: object, provider: str | None = None) -> str:
    """``gen_ai.provider.name`` for a call served under *config*.

    Like :func:`gen_ai_provider_name`, but resolves the Anthropic serving
    backend: ``LLM_ANTHROPIC_BACKEND=bedrock`` reports ``aws.bedrock`` and
    ``vertex`` reports ``gcp.vertex_ai``, as the spec requires.

    Args:
        config: The LLM config (``provider`` / ``anthropic_backend`` are read
            defensively, so a test double without them still works).
        provider: The provider that actually served the call, when it differs
            from the configured one (failover); defaults to ``config.provider``.
    """
    name = provider or getattr(config, "provider", None)
    if (name or "").lower() == "anthropic":
        backend = getattr(config, "anthropic_backend", None)
        if isinstance(backend, str) and backend in _ANTHROPIC_BACKEND_PROVIDER:
            return _ANTHROPIC_BACKEND_PROVIDER[backend]
    return gen_ai_provider_name(name if isinstance(name, str) else None)


#: Back-compat alias: the value is the same, only the attribute it feeds was
#: renamed (``gen_ai.system`` -> ``gen_ai.provider.name``).
gen_ai_system = gen_ai_provider_name


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

    The same call also reaches the usage sinks
    (:func:`core.services.llm.register_usage_sink`) as one metered turn, since
    the engine measured both sides itself.

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
    from core.services.llm.usage import Usage
    from core.services.llm.usage_sinks import UsageReport, emit_usage_report

    emit_usage_report(
        UsageReport(
            model=model,
            usage=Usage(
                input_tokens=max(int(prompt_tokens), 0),
                output_tokens=max(int(completion_tokens), 0),
            ),
        )
    )


def record_genai_metrics(
    provider: str,
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    batch: bool = False,
    duration_seconds: float | None = None,
    operation: str = "chat",
) -> None:
    """Emit OTel Gen AI semconv Prometheus metrics for one LLM call.

    Standard names (``gen_ai_client_token_usage``,
    ``gen_ai_client_operation_duration_seconds``) so semconv-aware dashboards
    light up without bespoke queries. Best-effort: metric registration/emit
    failures never break the request path.

    ``input_tokens`` is *fresh* input; cached prompt tokens are reported under
    their own ``gen_ai_token_type`` label values and priced at their own
    tier, so ``gen_ai_client_cost_usd_total`` agrees with the ``LoopBudget``
    and the tenant ledger instead of billing every cache read as full input.
    ``batch`` applies the batch API's 50% discount to the cost counter for
    the same reason: one price per call, in every ledger that reports it.
    """
    try:
        from core.observability.metrics import (
            GEN_AI_OPERATION_DURATION,
            GEN_AI_TOKEN_USAGE,
        )

        for count, token_type in (
            (input_tokens, "input"),
            (output_tokens, "output"),
            (cache_read_tokens, "cache_read"),
            (cache_write_tokens, "cache_write"),
        ):
            if count > 0:
                GEN_AI_TOKEN_USAGE.labels(provider, model, token_type).observe(count)
        if duration_seconds is not None:
            GEN_AI_OPERATION_DURATION.labels(provider, model, operation).observe(
                duration_seconds
            )
        if input_tokens > 0 or output_tokens > 0 or cache_read_tokens > 0:
            from core.models.pricing import estimate_cost, is_priced
            from core.observability.metrics import GEN_AI_COST_USD

            if is_priced(model):
                cost = estimate_cost(
                    model,
                    max(input_tokens, 0),
                    max(output_tokens, 0),
                    cache_read_tokens=max(cache_read_tokens, 0),
                    cache_write_tokens=max(cache_write_tokens, 0),
                    batch=batch,
                )
                if cost > 0:
                    GEN_AI_COST_USD.labels(provider, model).inc(cost)
    except Exception:  # pragma: no cover - metrics must never break requests
        pass


__all__ = [
    "gen_ai_provider_for",
    "gen_ai_provider_name",
    "gen_ai_system",
    "record_genai_metrics",
    "register_token_sink",
    "report_external_usage",
    "report_tokens_to_middleware",
    "unregister_token_sink",
]
