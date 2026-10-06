"""OpenTelemetry GenAI semantic-convention attribute names, in one place.

Every LLM and embedding span in the runtime takes its ``gen_ai.*`` keys from
here, so a rename in the spec is a one-file change instead of a grep across
the service layer. The names track the OpenTelemetry GenAI semantic
conventions (https://github.com/open-telemetry/semantic-conventions-genai,
``docs/registry/attributes/gen-ai.md``), which moved out of the core
``semantic-conventions`` repository after v1.41.

Deprecation window
------------------
Two renames are still dual-emitted so dashboards and alerts built on the old
keys keep working while they migrate:

* ``gen_ai.system`` -> ``gen_ai.provider.name`` (renamed in semconv v1.37).
* ``gen_ai.usage.cache_creation.input_tokens`` ->
  ``gen_ai.usage.cache_write.input_tokens`` (renamed in the GenAI repo).

The deprecated keys carry the same value as their replacement and will stop
being emitted in a future minor release; build new queries on the current
names only.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "GEN_AI_OPERATION_NAME",
    "GEN_AI_PROVIDER_NAME",
    "GEN_AI_REQUEST_MODEL",
    "GEN_AI_RESPONSE_FINISH_REASONS",
    "GEN_AI_RESPONSE_MODEL",
    "GEN_AI_SYSTEM_DEPRECATED",
    "GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS_DEPRECATED",
    "GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS",
    "GEN_AI_USAGE_CACHE_WRITE_INPUT_TOKENS",
    "GEN_AI_USAGE_INPUT_TOKENS",
    "GEN_AI_USAGE_OUTPUT_TOKENS",
    "METRIC_LABEL_PROVIDER_NAME",
    "provider_attributes",
]

#: The operation, e.g. ``chat`` or ``embeddings``.
GEN_AI_OPERATION_NAME: Final = "gen_ai.operation.name"
#: The provider, as a well-known value (``anthropic``, ``openai``,
#: ``aws.bedrock``, ``gcp.vertex_ai``, ``gcp.gemini``...) or a custom one.
GEN_AI_PROVIDER_NAME: Final = "gen_ai.provider.name"
#: Deprecated predecessor of :data:`GEN_AI_PROVIDER_NAME`; dual-emitted.
GEN_AI_SYSTEM_DEPRECATED: Final = "gen_ai.system"
GEN_AI_REQUEST_MODEL: Final = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL: Final = "gen_ai.response.model"
#: ``string[]`` — one reason per generation, in response order.
GEN_AI_RESPONSE_FINISH_REASONS: Final = "gen_ai.response.finish_reasons"

#: All prompt tokens, **including** cache reads and cache writes.
GEN_AI_USAGE_INPUT_TOKENS: Final = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS: Final = "gen_ai.usage.output_tokens"
#: Prompt tokens served from a provider-managed cache (a subset of input).
GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS: Final = "gen_ai.usage.cache_read.input_tokens"
#: Prompt tokens written to a provider-managed cache (a subset of input).
GEN_AI_USAGE_CACHE_WRITE_INPUT_TOKENS: Final = "gen_ai.usage.cache_write.input_tokens"
#: Deprecated predecessor of :data:`GEN_AI_USAGE_CACHE_WRITE_INPUT_TOKENS`.
GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS_DEPRECATED: Final = (
    "gen_ai.usage.cache_creation.input_tokens"
)

#: Prometheus label carrying ``gen_ai.provider.name`` on the ``gen_ai_client_*``
#: metrics (dots become underscores in the Prometheus exposition).
METRIC_LABEL_PROVIDER_NAME: Final = "gen_ai_provider_name"


def provider_attributes(provider: str) -> dict[str, str]:
    """Span attributes naming *provider* under both the current and old key.

    Args:
        provider: An already-normalized provider value (see
            ``core.services.llm._telemetry.gen_ai_provider_name``).

    Returns:
        ``{"gen_ai.provider.name": provider, "gen_ai.system": provider}`` —
        the second key only for the deprecation window.
    """
    return {GEN_AI_PROVIDER_NAME: provider, GEN_AI_SYSTEM_DEPRECATED: provider}
