"""Cache-read rates that a model publishes must not be derived.

The table prices a cache read at ``0.1x`` input unless the row says otherwise.
That default is right for the Opus, Sonnet and Haiku families, but Claude Fable
5.1 publishes a cache-read rate of ``$0.25`` per million tokens against a
``$10`` input rate — ``0.025x``, not ``0.1x``. Deriving it charged four times
the real rate, which matters twice over: the tenant budget gate aborts a run
that is still inside its budget, and the reported cost of an agent loop — whose
whole point is a long cached prefix — is inflated by the largest single term.
"""

import pytest

from core.models.pricing import DEFAULT_PRICING, get_price

pytestmark = pytest.mark.unit

#: What each model publishes per million cache-read tokens. Only Fable 5.1
#: departs from the 0.1x derivation; the other three are listed because their
#: published rate *coincides* with it, which is what makes the derivation safe
#: to keep as the default — if the multiplier is ever changed, these fail.
PUBLISHED_CACHE_READ: dict[str, float] = {
    "claude-fable-5-1": 0.25,
    "claude-opus-5": 0.5,
    "claude-sonnet-5": 0.2,
    "claude-haiku-4-5": 0.1,
}


@pytest.mark.parametrize(("model_id", "expected"), PUBLISHED_CACHE_READ.items())
def test_cache_read_is_billed_at_the_published_rate(
    model_id: str, expected: float
) -> None:
    """Each model must bill cache reads at exactly the rate it publishes."""
    price = get_price(model_id)
    assert price.effective_cache_read_usd_per_million == pytest.approx(expected)


def test_fable_cache_read_is_a_quarter_of_the_derived_default() -> None:
    """Pin the specific regression: 0.25 published against 1.00 derived."""
    price = get_price("claude-fable-5-1")
    derived = price.input_usd_per_million * 0.1

    assert derived == pytest.approx(1.0)
    assert price.effective_cache_read_usd_per_million == pytest.approx(0.25)


def test_a_cached_agent_turn_is_priced_at_the_published_rate() -> None:
    """The error only shows up at scale: 100K cached tokens, one turn."""
    price = get_price("claude-fable-5-1")

    cost = price.estimate(
        prompt_tokens=0, completion_tokens=0, cache_read_tokens=100_000
    )

    assert cost == pytest.approx(0.025)


def test_models_without_a_published_rate_still_derive_it() -> None:
    """The 0.1x fallback must stay for rows that do not name a rate."""
    unlisted = DEFAULT_PRICING["gpt-4o"]

    assert unlisted.cache_read_usd_per_million is None
    assert unlisted.effective_cache_read_usd_per_million == pytest.approx(
        unlisted.input_usd_per_million * 0.1
    )
