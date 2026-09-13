"""Neutral token-accounting record for a single model call.

The LLM stack historically carried one integer (``tokens_used``) per call,
summing prompt, completion and both cache counters into a single number. That
number cannot be priced: a cache *read* bills at ~0.1x input and a cache
*write* at ~1.25x, and output is 5x input on most families — so a total tells
you how many tokens moved, never how much they cost. It also cannot be split
back apart, which is why every caller re-derived output as
``total - estimate(prompt)``: an estimate subtracted from an exact figure,
wrong in both directions.

:class:`Usage` keeps the four buckets separate, exactly as the providers
report them, plus an ``estimated`` flag so a consumer can tell a metered
figure from a tokenizer guess. It is frozen and slotted: usage records are
passed around freely (results, spans, ledgers) and must never be mutated by a
consumer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["Usage", "billed_usage"]


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts for one model call, split by billing bucket.

    Attributes:
        input_tokens: Uncached prompt tokens billed at the input rate.
        output_tokens: Completion tokens (thinking tokens included, as the
            providers bill them).
        cache_read_tokens: Prompt tokens served from the provider's prompt
            cache (~0.1x input).
        cache_write_tokens: Prompt tokens written into the cache (~1.25x
            input).
        estimated: True when the numbers come from a local tokenizer estimate
            rather than the provider's metered usage.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    estimated: bool = False

    @property
    def total(self) -> int:
        """Every billed token in the call, across all four buckets."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    @property
    def prompt_tokens(self) -> int:
        """All prompt-side tokens (fresh input plus both cache buckets)."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def is_empty(self) -> bool:
        """True when nothing was recorded (no provider usage was available)."""
        return self.total == 0

    def merge(self, other: Usage) -> Usage:
        """Sum two usage records (multi-turn continuations, retries, batches).

        Args:
            other: The second record to add.

        Returns:
            Usage: Bucket-wise sum; ``estimated`` is sticky, because a total
            that contains one guess is itself a guess.
        """
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            estimated=self.estimated or other.estimated,
        )

    @classmethod
    def estimate(cls, input_tokens: int, output_tokens: int) -> Usage:
        """Build an explicitly-estimated record (no provider usage available).

        Args:
            input_tokens: Estimated prompt tokens.
            output_tokens: Estimated completion tokens.

        Returns:
            Usage: The record with ``estimated=True``.
        """
        return cls(
            input_tokens=input_tokens, output_tokens=output_tokens, estimated=True
        )

    @classmethod
    def from_anthropic(cls, raw: Any) -> Usage:
        """Read an Anthropic ``usage`` object (message or ``message_delta``).

        Anthropic reports ``cache_creation_input_tokens`` and
        ``cache_read_input_tokens`` *alongside* ``input_tokens`` (they are not
        included in it), so the four fields map one-to-one.

        Args:
            raw: The SDK usage object, or ``None`` when absent.

        Returns:
            Usage: The metered record, or an empty one when ``raw`` is None.
        """
        if raw is None:
            return cls()
        return cls(
            input_tokens=_int(getattr(raw, "input_tokens", 0)),
            output_tokens=_int(getattr(raw, "output_tokens", 0)),
            cache_write_tokens=_int(getattr(raw, "cache_creation_input_tokens", 0)),
            cache_read_tokens=_int(getattr(raw, "cache_read_input_tokens", 0)),
        )

    @classmethod
    def from_openai(cls, raw: Any) -> Usage:
        """Read an OpenAI ``usage`` object from a chat completion.

        OpenAI counts cached prompt tokens *inside* ``prompt_tokens``
        (``prompt_tokens_details.cached_tokens``), so they are subtracted out
        here — otherwise the cached prefix would be billed twice in any
        downstream pricing.

        Args:
            raw: The SDK usage object, or ``None`` when absent.

        Returns:
            Usage: The metered record, or an empty one when ``raw`` is None.
        """
        if raw is None:
            return cls()
        prompt_tokens = _int(getattr(raw, "prompt_tokens", 0))
        details = getattr(raw, "prompt_tokens_details", None)
        cached = _int(getattr(details, "cached_tokens", 0)) if details else 0
        cached = min(cached, prompt_tokens)
        return cls(
            input_tokens=prompt_tokens - cached,
            output_tokens=_int(getattr(raw, "completion_tokens", 0)),
            cache_read_tokens=cached,
        )


def _int(value: Any) -> int:
    """Coerce a provider counter to a non-negative int (``None`` → 0).

    Provider SDKs answer ``None`` for counters that do not apply, and test
    doubles answer with mocks; both must read as "nothing recorded" rather
    than blowing up the accounting path.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def billed_usage(
    usage: Usage | None, *, fallback_input: int, fallback_total: int
) -> Usage:
    """Resolve the four-bucket record to price and report for one call.

    Prefers the provider's metered record; falls back to the legacy derivation
    (estimated prompt, remainder as output) when no metered usage reached us,
    so callers keep working against providers that report only a total.

    This used to be ``usage_split``, which answered a two-value
    ``(input, output)`` tuple whose "input" was :attr:`Usage.prompt_tokens` —
    fresh input *plus* both cache buckets folded into one number. Every caller
    then forwarded that conflated figure as ``prompt_tokens`` and took the
    ``cache_read_tokens=0, cache_write_tokens=0`` defaults of
    :func:`core.models.pricing.estimate_cost`, so a cache read (~0.1x input)
    was priced at the full input rate everywhere: the ``LoopBudget``, the
    tenant ledger and ``gen_ai.usage.input_tokens``. The better the prompt
    cache worked, the worse the overcharge — on 200 fresh + 20 000 cached
    input tokens that is 4.8x on ``claude-sonnet-5``. A function handing back
    a number eleven call sites have to re-attribute correctly is the defect;
    a record cannot be mis-attributed, because the caller forwards the
    buckets by name.

    Args:
        usage: The metered record, or ``None``/estimated when unavailable.
        fallback_input: Locally estimated prompt tokens.
        fallback_total: Total tokens reported for the call.

    Returns:
        Usage: The metered record itself when the provider reported one,
        otherwise an explicitly-``estimated`` record with empty cache buckets.
    """
    if usage is not None and not usage.estimated and not usage.is_empty:
        return usage
    return Usage.estimate(fallback_input, max(fallback_total - fallback_input, 0))
