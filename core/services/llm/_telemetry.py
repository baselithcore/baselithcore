"""Shared LLM telemetry helpers.

Extracted from ``service.py`` so both the legacy string path and the structured
tool-calling path (``structured.py``) emit identical ``gen_ai.*`` attributes and
forward token usage to the request-scoped cost controller the same way.
"""

from __future__ import annotations

from core.middleware.cost_control import cost_controller

# OTel GenAI semantic-convention `gen_ai.system` values for our providers
# (https://opentelemetry.io/docs/specs/semconv/gen-ai/). Falls back to the raw
# configured provider name lowercased for anything not mapped here.
_GEN_AI_SYSTEM = {
    "anthropic": "anthropic",
    "openai": "openai",
    "ollama": "ollama",
    "huggingface": "huggingface",
}


def gen_ai_system(provider: str | None) -> str:
    """Normalize the configured provider to a ``gen_ai.system`` value."""
    key = (provider or "").lower()
    return _GEN_AI_SYSTEM.get(key, key or "unknown")


def report_tokens_to_middleware(count: int, model: str) -> None:
    """Forward token usage to the request-scoped middleware cost controller.

    Propagates ``BudgetExceededError`` so ``CostControlMiddleware`` can translate
    it into a 429 response.
    """
    if count <= 0:
        return
    cost_controller.track_tokens(count, model=model)


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
    except Exception:  # pragma: no cover - metrics must never break requests
        pass


__all__ = ["gen_ai_system", "record_genai_metrics", "report_tokens_to_middleware"]
