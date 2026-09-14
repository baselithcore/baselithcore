"""
Token Estimation Utilities.

This module provides a robust mechanism for estimating the number of tokens
in a given text string. Accurate token counting is essential for:
1. LLM Context Window Management: Avoiding truncation or out-of-memory errors.
2. Cost Optimization: Predicting and tracking usage for billing.
3. Performance: Efficiently chunking data for vectorization.

The system uses a tiered approach:
- Level 0 (Exact, Claude-only, ASYNC ONLY): For ``claude*`` models,
  :func:`estimate_tokens_async` calls
  ``anthropic.Anthropic().messages.count_tokens`` (thread-offloaded) when the
  SDK is installed, ``ANTHROPIC_API_KEY`` is set, AND the opt-in
  ``BASELITH_EXACT_TOKEN_COUNTING`` setting is enabled — see
  :func:`count_tokens_exact_available`. **The sync** :func:`estimate_tokens`
  **never makes this call**: several call sites invoke it synchronously from
  a hot path (e.g. once per streamed delta), and a blocking HTTP request
  there would stall the event loop. An API key alone does not enable this
  path — enabling it implicitly would turn every token estimate into a
  network call in most production deployments, which already set the key.
- Level 1 (Exact-ish): Uses `tiktoken` (cl100k_base/gpt-4) if the library is
  installed, calibrated per model family (see ``_MODEL_TOKENIZER_FACTORS``).
- Level 2 (Heuristic): Falls back to a character-class analysis (Code, CJK, Prose)
  that is significantly more accurate than the naive ``len // 4`` rule.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.observability.logging import get_logger

logger = get_logger(__name__)

# Above this size the exact tiktoken encode is offloaded to a worker thread:
# encoding is C-speed but O(len), so a multi-hundred-KB prompt holds the event
# loop for milliseconds per LLM call. Small texts stay inline — a thread hop
# would cost more than it saves.
_ASYNC_OFFLOAD_THRESHOLD_CHARS = 65_536

# Tiktoken encoder (lazy-loaded, cached)
_encoder = None
_tiktoken_available: bool | None = None

# Anthropic client for exact Claude token counting (lazy-loaded, cached for
# the lifetime of the process — see ``_load_anthropic_client``).
_anthropic_client: Any = None
_anthropic_client_checked = False


class ExactTokenCountingConfig(BaseSettings):
    """Opt-in switch for network-backed exact Claude token counting."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    enabled: bool = Field(
        default=False,
        alias="BASELITH_EXACT_TOKEN_COUNTING",
        description=(
            "Use anthropic.Anthropic().messages.count_tokens (via "
            "estimate_tokens_async) for claude* models instead of the "
            "tiktoken/heuristic estimate. Off by default: ANTHROPIC_API_KEY "
            "alone is not enough to enable it, since a key is present in "
            "most production deployments already and this call is a "
            "blocking network request. The sync estimate_tokens() never "
            "makes this call regardless of this setting."
        ),
    )


_exact_token_counting_config: ExactTokenCountingConfig | None = None


def get_exact_token_counting_config() -> ExactTokenCountingConfig:
    """Get or create the global exact-token-counting configuration."""
    global _exact_token_counting_config
    if _exact_token_counting_config is None:
        _exact_token_counting_config = ExactTokenCountingConfig()
    return _exact_token_counting_config


# LRU cache of exact counts, keyed by (model, sha256(text)) so the cache
# holds compact keys instead of arbitrarily large prompt text. Bounded at
# 1024 entries; evicted oldest-first.
_EXACT_TOKEN_CACHE_MAX_ENTRIES = 1024
_exact_token_cache: OrderedDict[tuple[str, str], int] = OrderedDict()
_exact_token_cache_lock = threading.Lock()

# A model id whose count_tokens call just failed is not retried on every
# subsequent call (a hot loop calling an unreachable/invalid model would
# otherwise pay a fresh timeout+retry per call). Bounded TTL rather than a
# permanent process-lifetime blacklist: a transient network blip recovers.
_EXACT_COUNT_FAILURE_TTL_SECONDS = 300.0
_exact_count_failed_at: dict[str, float] = {}
_exact_count_failure_lock = threading.Lock()


def _record_exact_count_failure(model: str) -> None:
    with _exact_count_failure_lock:
        _exact_count_failed_at[model] = time.monotonic()


def _exact_count_recently_failed(model: str) -> bool:
    with _exact_count_failure_lock:
        failed_at = _exact_count_failed_at.get(model)
    if failed_at is None:
        return False
    return (time.monotonic() - failed_at) < _EXACT_COUNT_FAILURE_TTL_SECONDS


# Per-family calibration on top of the cl100k count. tiktoken is OpenAI's
# tokenizer: for the same text, Claude's tokenizer produces ~15-20% more tokens
# (more on code), so an uncalibrated count silently under-budgets truncation
# and cost tracking for Claude models. 1.2 sits at the top of that range —
# over-estimating is the safe direction for a budget (worst case we truncate
# slightly early; the old behaviour risked overflowing the context window).
# Matched case-insensitively as substrings of the model id.
_MODEL_TOKENIZER_FACTORS: tuple[tuple[str, float], ...] = (
    ("claude", 1.2),
    ("anthropic", 1.2),
)


def _model_factor(model: str | None) -> float:
    """Calibration multiplier for ``model`` (1.0 when unknown/OpenAI-family)."""
    if not model:
        return 1.0
    lowered = model.lower()
    for marker, factor in _MODEL_TOKENIZER_FACTORS:
        if marker in lowered:
            return factor
    return 1.0


def _get_tiktoken_encoder() -> Any:
    """
    Attempt to load and cache the tiktoken cl100k_base encoder.
    """
    global _encoder, _tiktoken_available
    if _tiktoken_available is None:
        try:
            import tiktoken

            _encoder = tiktoken.encoding_for_model("gpt-4")
            _tiktoken_available = True
        except (ImportError, Exception):
            _tiktoken_available = False
    return _encoder


def _load_anthropic_client() -> Any:
    """Lazily construct (and cache for the process lifetime) the Anthropic
    client used for exact token counting, or ``None`` when unusable.

    Constructing the client never makes a network call, so this is cheap
    once memoized; it is only unusable when the SDK is not installed or no
    ``ANTHROPIC_API_KEY`` is set (the same env var the SDK itself reads).
    """
    global _anthropic_client, _anthropic_client_checked
    if _anthropic_client_checked:
        return _anthropic_client
    _anthropic_client_checked = True
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from anthropic import Anthropic

        _anthropic_client = Anthropic(timeout=5.0)
    except Exception as exc:
        logger.debug("anthropic_client_unavailable", extra={"error": str(exc)})
        _anthropic_client = None
    return _anthropic_client


def count_tokens_exact_available() -> bool:
    """Whether exact Claude token counting can run in this process.

    True only when ``BASELITH_EXACT_TOKEN_COUNTING`` is enabled AND the
    ``anthropic`` SDK is installed AND ``ANTHROPIC_API_KEY`` is set — the
    setting gates first, and cheaply, so an environment that merely has a key
    (the common production case) does not silently enable a per-call network
    request. Diagnostic helper for operators/dashboards to explain why token
    counts for ``claude*`` models are heuristic-only in a given environment.
    """
    if not get_exact_token_counting_config().enabled:
        return False
    return _load_anthropic_client() is not None


def _count_tokens_exact(text: str, model: str) -> int | None:
    """Exact input-token count for *text* on *model* via the Anthropic SDK.

    **Blocking network I/O.** Only ever called from
    :func:`estimate_tokens_async`, and only via ``asyncio.to_thread`` — never
    from the sync :func:`estimate_tokens`.

    Returns ``None`` when the SDK/API key/setting are unavailable, the model
    id recently failed (see ``_exact_count_recently_failed`` — a bounded TTL,
    so a hot loop calling an unreachable/invalid model does not pay a fresh
    timeout on every call), or the call itself fails, so the caller can fall
    back to the heuristic. Successful results are cached (LRU, bounded,
    keyed by ``(model, sha256(text))``) so repeated estimates for the same
    call never re-hit the network.
    """
    client = _load_anthropic_client()
    if client is None:
        return None
    if _exact_count_recently_failed(model):
        return None

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = (model, digest)
    with _exact_token_cache_lock:
        cached = _exact_token_cache.get(key)
        if cached is not None:
            _exact_token_cache.move_to_end(key)
            return cached

    try:
        result = client.messages.count_tokens(
            model=model, messages=[{"role": "user", "content": text}]
        )
        count = int(result.input_tokens)
    except Exception as exc:
        logger.debug(
            "exact_token_count_failed", extra={"model": model, "error": str(exc)}
        )
        _record_exact_count_failure(model)
        return None

    with _exact_token_cache_lock:
        _exact_token_cache[key] = count
        _exact_token_cache.move_to_end(key)
        while len(_exact_token_cache) > _EXACT_TOKEN_CACHE_MAX_ENTRIES:
            _exact_token_cache.popitem(last=False)
    return count


def estimate_tokens(text: str, model: str | None = None) -> int:
    """
    Predict the token count for a piece of text.

    **Never performs network I/O** — this is the sync entry point called
    from hot, often-synchronous paths (e.g. once per streamed delta), and a
    blocking HTTP call there would stall whatever loop it runs on. It always
    uses tiktoken if available, calibrated per model family; if tiktoken is
    missing or fails (e.g. specialized model errors), it falls back to a
    heuristic that adjusts for the content type (Code vs Prose). Exact
    Claude counting via the Anthropic SDK is available only through
    :func:`estimate_tokens_async` (thread-offloaded).

    Args:
        text: The raw string to analyze.
        model: Optional model identifier to guide tokenization strategy.

    Returns:
        int: The estimated token count, guaranteed to be at least 1 for non-empty text.
    """
    if not text:
        return 0

    factor = _model_factor(model)

    # Try exact counting with tiktoken, calibrated per model family.
    encoder = _get_tiktoken_encoder()
    if encoder is not None:
        try:
            return max(1, round(len(encoder.encode(text)) * factor))
        except Exception:
            pass  # Fall through to heuristic

    return max(1, round(_heuristic_token_count(text) * factor))


def _exact_counting_would_apply(text: str, model: str | None) -> bool:
    """Whether ``estimate_tokens_async`` would attempt an exact SDK count.

    Used to decide whether to call :func:`_count_tokens_exact` (via
    ``asyncio.to_thread``) at all, and to force the thread-offload path
    regardless of text length — a blocking HTTP call must never run inline
    on the event loop, unlike the pure-CPU tiktoken/heuristic paths the
    length threshold in :func:`estimate_tokens_async` is calibrated for.
    """
    if not text or not model:
        return False
    return "claude" in model.lower() and count_tokens_exact_available()


async def estimate_tokens_async(text: str, model: str | None = None) -> int:
    """Async token estimate; the only entry point that may reach the network.

    For ``claude*`` models, when :func:`count_tokens_exact_available` is
    True (opt-in ``BASELITH_EXACT_TOKEN_COUNTING`` + SDK + API key), calls
    the exact Anthropic tokenizer via ``asyncio.to_thread`` — never inline,
    so the event loop is never blocked by the HTTP request. On any failure,
    or when exact counting does not apply, falls back to the pure-CPU
    :func:`estimate_tokens`, itself thread-offloaded above
    ``_ASYNC_OFFLOAD_THRESHOLD_CHARS`` (encoding is C-speed but O(len), so a
    multi-hundred-KB prompt still holds the loop for milliseconds).

    Args:
        text: The raw string to analyze.
        model: Optional model identifier to guide tokenization strategy.

    Returns:
        int: The estimated token count, at least 1 for non-empty text.
    """
    if not text:
        return 0

    if _exact_counting_would_apply(text, model):
        assert model is not None  # narrowed by _exact_counting_would_apply
        exact = await asyncio.to_thread(_count_tokens_exact, text, model)
        if exact is not None:
            return exact

    if len(text) < _ASYNC_OFFLOAD_THRESHOLD_CHARS:
        return estimate_tokens(text, model)
    return await asyncio.to_thread(estimate_tokens, text, model)


# Pre-compiled patterns for the heuristic
_CJK_RANGE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff"
    r"\uac00-\ud7af\u1100-\u11ff]"
)
_CODE_INDICATORS = re.compile(r"[{}()\[\];=<>|&^~]")


@lru_cache(maxsize=256)
def _classify_text(text_hash: int, code_ratio: float, cjk_ratio: float) -> float:
    """
    Determine the optimal chars-per-token ratio for a text sample.

    Calculates weight based on:
    - CJK (Chinese, Japanese, Korean): Very high token density (~1.5 chars/token).
    - Code: High density due to punctuation/symbols (~3 chars/token).
    - Prose: Standard English density (~4 chars/token).

    Returns:
        float: Estimated average characters per token.
    """
    if cjk_ratio > 0.3:
        return 1.5
    if code_ratio > 0.05:
        return 3.0
    return 4.0


def _heuristic_token_count(text: str) -> int:
    """
    Execute a character-class based token estimation.

    This algorithm is more resilient than standard counts because it
    adjusts for high-symbol environments (Code) and multi-byte characters (CJK).

    Args:
        text: The text to estimate.

    Returns:
        int: Calculated count based on classified ratios.
    """
    length = len(text)
    if length == 0:
        return 0

    # Sample up to 500 chars for classification (performance)
    sample = text[:500] if length > 500 else text
    sample_len = len(sample)

    cjk_count = len(_CJK_RANGE.findall(sample))
    code_count = len(_CODE_INDICATORS.findall(sample))

    cjk_ratio = cjk_count / sample_len
    code_ratio = code_count / sample_len

    chars_per_token = _classify_text(hash(sample), code_ratio, cjk_ratio)
    return max(1, int(length / chars_per_token))
