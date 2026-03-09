"""
Token Estimation Utilities.

This module provides a robust mechanism for estimating the number of tokens
in a given text string. Accurate token counting is essential for:
1. LLM Context Window Management: Avoiding truncation or out-of-memory errors.
2. Cost Optimization: Predicting and tracking usage for billing.
3. Performance: Efficiently chunking data for vectorization.

The system uses a tiered approach:
- Level 1 (Exact): Uses `tiktoken` (cl100k_base/gpt-4) if the library is installed.
- Level 2 (Heuristic): Falls back to a character-class analysis (Code, CJK, Prose)
  that is significantly more accurate than the naive ``len // 4`` rule.
"""

from __future__ import annotations

from core.observability.logging import get_logger
import re
from functools import lru_cache
from typing import Optional

logger = get_logger(__name__)

# Tiktoken encoder (lazy-loaded, cached)
_encoder = None
_tiktoken_available: Optional[bool] = None


def _get_tiktoken_encoder():
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


def estimate_tokens(text: str, model: Optional[str] = None) -> int:
    """
    Predict the token count for a piece of text.

    This function attempts to use the exact tokenizer (tiktoken) if available.
    If tiktoken is missing or fails (e.g. specialized model errors), it
    switches to a heuristic that adjusts for the content type (Code vs Prose).

    Args:
        text: The raw string to analyze.
        model: Optional model identifier to guide tokenization strategy.

    Returns:
        int: The estimated token count, guaranteed to be at least 1 for non-empty text.
    """
    if not text:
        return 0

    # Try exact counting with tiktoken
    encoder = _get_tiktoken_encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass  # Fall through to heuristic

    return _heuristic_token_count(text)


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
