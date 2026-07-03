"""
LLM pricing table and cost estimation.

Cost-aware model selection requires up-to-date prices. Prices are encoded
as a data table here rather than scattered through business code so a
quarterly refresh is a single PR.

Prices are expressed in USD per 1M tokens. The table covers the most
common production models; unknown models fall back to ``UNKNOWN_PRICE``
to make missing entries visible (cost looks suspiciously high until
patched).

Treat this table as a default. Production deployments should override
via configuration when negotiated rates differ from list price.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class ModelPrice:
    """Per-1M-token cost in USD for a single model."""

    input_usd_per_million: float
    output_usd_per_million: float

    def estimate(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Return USD cost for a single call with the given token counts."""
        if prompt_tokens < 0 or completion_tokens < 0:
            raise ValueError("token counts must be non-negative")
        return (
            prompt_tokens * self.input_usd_per_million / 1_000_000.0
            + completion_tokens * self.output_usd_per_million / 1_000_000.0
        )


UNKNOWN_PRICE: Final[ModelPrice] = ModelPrice(
    input_usd_per_million=100.0,
    output_usd_per_million=100.0,
)


# Snapshot date of DEFAULT_PRICING. Refresh quarterly, updating both together —
# consumers (e.g. dashboards) display this instead of hand-syncing a copy.
PRICING_AS_OF: Final[str] = "2026-05-16"

DEFAULT_PRICING: Final[Mapping[str, ModelPrice]] = {
    # Anthropic
    "claude-opus-4-7": ModelPrice(15.0, 75.0),
    "claude-opus-4-6": ModelPrice(15.0, 75.0),
    "claude-sonnet-4-6": ModelPrice(3.0, 15.0),
    "claude-haiku-4-5": ModelPrice(0.80, 4.0),
    # OpenAI
    "gpt-5": ModelPrice(10.0, 30.0),
    "gpt-4o": ModelPrice(2.50, 10.0),
    "gpt-4o-mini": ModelPrice(0.15, 0.60),
    # Google
    "gemini-2.5-pro": ModelPrice(3.50, 10.50),
    "gemini-2.5-flash": ModelPrice(0.075, 0.30),
    # Local (Ollama, etc.) — zero marginal cost; capacity-bound, not price-bound
    "ollama/llama-3-70b": ModelPrice(0.0, 0.0),
    "ollama/mistral-large": ModelPrice(0.0, 0.0),
}


def get_price(
    model_id: str, *, table: Mapping[str, ModelPrice] = DEFAULT_PRICING
) -> ModelPrice:
    """Return the ``ModelPrice`` for ``model_id`` or ``UNKNOWN_PRICE``."""
    return table.get(model_id, UNKNOWN_PRICE)


def estimate_cost(
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    table: Mapping[str, ModelPrice] = DEFAULT_PRICING,
) -> float:
    """Estimate the USD cost of a single call."""
    return get_price(model_id, table=table).estimate(prompt_tokens, completion_tokens)
