"""
LLM pricing table and cost estimation.

Cost-aware model selection requires up-to-date prices. Prices are encoded
as a data table here rather than scattered through business code so a
quarterly refresh is a single PR.

Prices are expressed in USD per 1M tokens. The table covers the most
common production models; unknown models fall back to ``UNKNOWN_PRICE``
to make missing entries visible (cost looks suspiciously high until
patched).

Beyond the base input/output rate, ``ModelPrice`` also carries the two
tiers Anthropic (and most vendors) bill for prompt caching plus a batch
discount:

- **cache read** — tokens served from a previous cache write. Defaults to
  0.1x the input rate when not given explicitly.
- **cache write** — tokens written into the cache for the first time.
  Defaults to 1.25x the input rate when not given explicitly.
- **batch** — the Message Batches API discount, applied uniformly to the
  whole estimate (input, output and cache tiers alike) when ``batch=True``.

Treat this table as a default. Production deployments should override
via configuration when negotiated rates differ from list price.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

# Derived-default multipliers used when a model does not specify its own
# cache read/write rate. See the Global Constraints: "Cache read ~= 0.1x
# input, cache write ~= 1.25x input".
_DEFAULT_CACHE_READ_MULTIPLIER: Final[float] = 0.1
_DEFAULT_CACHE_WRITE_MULTIPLIER: Final[float] = 1.25


@dataclass(frozen=True)
class ModelPrice:
    """Per-1M-token cost in USD for a single model.

    Args:
        input_usd_per_million: Standard input token rate.
        output_usd_per_million: Standard output token rate.
        cache_read_usd_per_million: Rate for tokens read from cache. ``None``
            (the default) derives ``0.1 * input_usd_per_million``.
        cache_write_usd_per_million: Rate for tokens written to cache.
            ``None`` (the default) derives ``1.25 * input_usd_per_million``.
        batch_multiplier: Multiplier applied to the whole estimate when a
            call is priced via ``estimate(..., batch=True)``. Defaults to
            0.5 (the Anthropic Message Batches API discount).
    """

    input_usd_per_million: float
    output_usd_per_million: float
    cache_read_usd_per_million: float | None = None
    cache_write_usd_per_million: float | None = None
    batch_multiplier: float = 0.5

    @property
    def effective_cache_read_usd_per_million(self) -> float:
        """Cache-read rate: the explicit value, or 0.1x input when unset."""
        if self.cache_read_usd_per_million is not None:
            return self.cache_read_usd_per_million
        return self.input_usd_per_million * _DEFAULT_CACHE_READ_MULTIPLIER

    @property
    def effective_cache_write_usd_per_million(self) -> float:
        """Cache-write rate: the explicit value, or 1.25x input when unset."""
        if self.cache_write_usd_per_million is not None:
            return self.cache_write_usd_per_million
        return self.input_usd_per_million * _DEFAULT_CACHE_WRITE_MULTIPLIER

    def estimate(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        batch: bool = False,
    ) -> float:
        """Return USD cost for a single call with the given token counts.

        Args:
            prompt_tokens: Non-cached input tokens.
            completion_tokens: Output tokens.
            cache_read_tokens: Tokens served from a prompt-cache hit, billed
                at :attr:`effective_cache_read_usd_per_million`.
            cache_write_tokens: Tokens newly written into the prompt cache,
                billed at :attr:`effective_cache_write_usd_per_million`.
            batch: Whether this call was priced via the batch API; the whole
                total is scaled by :attr:`batch_multiplier`.
        """
        for tokens in (
            prompt_tokens,
            completion_tokens,
            cache_read_tokens,
            cache_write_tokens,
        ):
            if tokens < 0:
                raise ValueError("token counts must be non-negative")
        total = (
            prompt_tokens * self.input_usd_per_million
            + completion_tokens * self.output_usd_per_million
            + cache_read_tokens * self.effective_cache_read_usd_per_million
            + cache_write_tokens * self.effective_cache_write_usd_per_million
        ) / 1_000_000.0
        return total * self.batch_multiplier if batch else total


UNKNOWN_PRICE: Final[ModelPrice] = ModelPrice(
    input_usd_per_million=100.0,
    output_usd_per_million=100.0,
)

#: Zero rate for a model served by a local provider.
LOCAL_PRICE: Final[ModelPrice] = ModelPrice(0.0, 0.0)

#: Providers whose tokens carry no marginal dollar cost. Self-hosted inference
#: is capacity-bound (GPU seconds, VRAM, a queue), not price-bound, so billing
#: it through the unknown-model policy charges ``UNKNOWN_PRICE`` — a
#: deliberately punitive 100 $/M that exists to make a *missing vendor row*
#: visible. Applied to a local model it invents spend that never happened, and
#: a per-run or per-tenant budget aborts on it. Local models are priced at zero
#: instead, and their real cost is watched as tokens, not dollars.
LOCAL_PROVIDERS: Final[frozenset[str]] = frozenset({"ollama", "vllm"})


def is_local_model_id(model_id: str) -> bool:
    """Whether *model_id* names a model served by a local provider.

    Local ids are namespaced (``ollama/llama3.2``) because a bare tag carries
    no vendor: ``llama3.2`` costs nothing served locally and bills behind a
    hosted gateway. Use :func:`qualified_model_id` to build one.
    """
    return any(model_id.startswith(f"{provider}/") for provider in LOCAL_PROVIDERS)


def qualified_model_id(provider: str | None, model: str) -> str:
    """The pricing-table key for *model* as served by *provider*.

    Local providers get their model namespaced; hosted providers keep the bare
    id their pricing rows are keyed by. Idempotent, so a caller may apply it to
    an id that is already qualified.

    Args:
        provider: The provider that actually served the call, or ``None``.
        model: The model id the provider was asked for.

    Returns:
        ``"<provider>/<model>"`` for a local provider, else ``model``.
    """
    if not provider or provider not in LOCAL_PROVIDERS:
        return model
    prefix = f"{provider}/"
    return model if model.startswith(prefix) else f"{prefix}{model}"


# Snapshot date of DEFAULT_PRICING. Refresh quarterly, updating both together —
# consumers (e.g. dashboards) display this instead of hand-syncing a copy.
# Anthropic rows verified against the official model catalog on this date;
# other vendors carried over from the 2026-05-16 snapshot.
PRICING_AS_OF: Final[str] = "2026-09-13"

DEFAULT_PRICING: Final[Mapping[str, ModelPrice]] = {
    # Anthropic ($/1M tokens, input/output) — verified against the official
    # model catalog on PRICING_AS_OF. Cache read/write use the derived
    # defaults (0.1x / 1.25x input) unless the row names its own rate; batch
    # uses the derived default (0.5x).
    #
    # Claude Fable 5.1 is the row that breaks the 0.1x cache-read derivation:
    # it publishes $0.25/MTok against a $10 input rate (0.025x), so deriving
    # the rate billed 4x — on the term that dominates an agent loop, whose
    # prefix is cached by design, and on the number the tenant budget gate
    # aborts against (rate verified 2026-09-22).
    "claude-fable-5-1": ModelPrice(10.0, 50.0, cache_read_usd_per_million=0.25),
    "claude-fable-5": ModelPrice(10.0, 50.0),
    # Mythos 5.1 prices input and output exactly as Fable 5.1 does, but its
    # cache-read rate was left open at launch and is not published, so the
    # derived 0.1x stands rather than an assumed 0.25. Over-charging closes a
    # budget early, which the operator sees and can raise; under-charging lets
    # a run overspend a cap silently.
    "claude-mythos-5-1": ModelPrice(10.0, 50.0),
    "claude-mythos-5": ModelPrice(10.0, 50.0),
    "claude-opus-5": ModelPrice(5.0, 25.0),
    "claude-opus-4-8": ModelPrice(5.0, 25.0),
    "claude-opus-4-7": ModelPrice(5.0, 25.0),
    "claude-opus-4-6": ModelPrice(5.0, 25.0),
    "claude-sonnet-5": ModelPrice(2.0, 10.0),
    "claude-sonnet-4-6": ModelPrice(3.0, 15.0),
    "claude-haiku-4-5": ModelPrice(1.0, 5.0),
    # OpenAI
    "gpt-5": ModelPrice(10.0, 30.0),
    "gpt-4o": ModelPrice(2.50, 10.0),
    "gpt-4o-mini": ModelPrice(0.15, 0.60),
    # Google
    "gemini-2.5-pro": ModelPrice(3.50, 10.50),
    "gemini-2.5-flash": ModelPrice(0.075, 0.30),
    # Local models need no rows: every ``<local-provider>/<model>`` id is
    # priced at zero by ``get_price`` (see LOCAL_PROVIDERS). Listing a few tags
    # here used to imply the opposite — that an unlisted local tag was
    # *unpriced*, which is how a self-hosted model came to be billed at
    # UNKNOWN_PRICE.
}


def get_price(
    model_id: str, *, table: Mapping[str, ModelPrice] = DEFAULT_PRICING
) -> ModelPrice:
    """Return the ``ModelPrice`` for ``model_id``.

    Resolution order: an explicit table row, then :data:`LOCAL_PRICE` for a
    locally-served model, then :data:`UNKNOWN_PRICE`.
    """
    price = table.get(model_id)
    if price is not None:
        return price
    if is_local_model_id(model_id):
        return LOCAL_PRICE
    return UNKNOWN_PRICE


def is_priced(
    model_id: str, *, table: Mapping[str, ModelPrice] = DEFAULT_PRICING
) -> bool:
    """Whether a cost lookup for *model_id* is backed by a real rate.

    True for a table row and for any locally-served model (whose rate is a
    known zero). The unknown-model policy therefore applies only to what is
    left: a hosted model with no row, which is the case worth warning about.
    """
    return model_id in table or is_local_model_id(model_id)


def estimate_cost(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    batch: bool = False,
    table: Mapping[str, ModelPrice] = DEFAULT_PRICING,
) -> float:
    """Estimate the USD cost of a single call.

    ``cache_read_tokens``/``cache_write_tokens`` line up with the field
    names on the ``Usage`` dataclass produced by the LLM service layer, so a
    caller can forward ``usage.cache_read_tokens`` / ``usage.cache_write_tokens``
    directly as keyword arguments.
    """
    return get_price(model_id, table=table).estimate(
        input_tokens,
        output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        batch=batch,
    )
