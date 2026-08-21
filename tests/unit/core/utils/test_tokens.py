"""
Comprehensive tests for core.utils.tokens module.

Tests cover:
- estimate_tokens() with various inputs
- tiktoken availability and fallback
- _heuristic_token_count() with different text types
- _classify_text() edge cases and caching
- Empty string handling
- Very long text handling
"""

from unittest.mock import MagicMock, patch

import pytest

import core.utils.tokens as tokens_module

_classify_text = tokens_module._classify_text
_get_tiktoken_encoder = tokens_module._get_tiktoken_encoder
_heuristic_token_count = tokens_module._heuristic_token_count
estimate_tokens = tokens_module.estimate_tokens
estimate_tokens_async = tokens_module.estimate_tokens_async


@pytest.fixture(autouse=True)
def _restore_tokenizer_globals():
    """The tests below mutate the module's cached encoder state; leaking a
    MagicMock encoder (or a forced heuristic mode) breaks every later
    token-count test in the process under random test ordering."""
    saved = (tokens_module._encoder, tokens_module._tiktoken_available)
    yield
    tokens_module._encoder, tokens_module._tiktoken_available = saved


class TestEstimateTokens:
    """Tests for the main estimate_tokens() function."""

    def test_empty_string(self):
        """Empty string should return 0 tokens."""
        assert estimate_tokens("") == 0

    def test_simple_english_prose(self):
        """Normal English prose should use heuristic estimation."""
        text = "The quick brown fox jumps over the lazy dog."
        tokens = estimate_tokens(text)
        # Prose: ~4 chars/token, so ~47 chars / 4 = ~11-12 tokens
        assert 10 <= tokens <= 15

    def test_very_short_text(self):
        """Very short text should return at least 1 token."""
        assert estimate_tokens("Hi") >= 1
        assert estimate_tokens("a") >= 1

    def test_long_text(self):
        """Very long text (>10k chars) should be estimated correctly."""
        long_text = "A" * 10000
        tokens = estimate_tokens(long_text)
        # Either tiktoken or heuristic: reasonable range for 10k chars
        assert 1000 <= tokens <= 3000

    def test_with_model_parameter(self):
        """Model parameter should be accepted (currently unused but API-compatible)."""
        text = "Test text"
        tokens = estimate_tokens(text, model="gpt-4")
        assert tokens > 0

    @patch("core.utils.tokens._get_tiktoken_encoder")
    def test_tiktoken_available_success(self, mock_get_encoder):
        """When tiktoken is available, use exact counting."""
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = [1, 2, 3, 4, 5]  # 5 tokens
        mock_get_encoder.return_value = mock_encoder

        text = "Test text with tiktoken"
        tokens = estimate_tokens(text)

        assert tokens == 5
        mock_encoder.encode.assert_called_once_with(text)

    @patch("core.utils.tokens._get_tiktoken_encoder")
    def test_tiktoken_available_but_encode_fails(self, mock_get_encoder):
        """If tiktoken.encode() fails, fall back to heuristic."""
        mock_encoder = MagicMock()
        mock_encoder.encode.side_effect = Exception("Encoding error")
        mock_get_encoder.return_value = mock_encoder

        text = "Test text with encoding failure"
        tokens = estimate_tokens(text)

        # Should fall back to heuristic: ~34 chars / 4 = ~8 tokens
        assert 6 <= tokens <= 10

    @patch("core.utils.tokens._get_tiktoken_encoder")
    def test_tiktoken_unavailable_fallback(self, mock_get_encoder):
        """When tiktoken is unavailable, use heuristic."""
        mock_get_encoder.return_value = None

        text = "Test text without tiktoken"
        tokens = estimate_tokens(text)

        # Heuristic: ~27 chars / 4 = ~6-7 tokens
        assert 5 <= tokens <= 9


class TestGetTiktokenEncoder:
    """Tests for _get_tiktoken_encoder() function."""

    def test_tiktoken_import_success(self):
        """When tiktoken is available, encoder should be loaded."""
        # Reset global state

        tokens_module._encoder = None
        tokens_module._tiktoken_available = None

        # Mock tiktoken module
        mock_tiktoken = MagicMock()
        mock_encoder = MagicMock()
        mock_tiktoken.encoding_for_model.return_value = mock_encoder

        with patch.dict("sys.modules", {"tiktoken": mock_tiktoken}):
            encoder = _get_tiktoken_encoder()

            assert encoder is mock_encoder
            assert tokens_module._tiktoken_available is True

    def test_tiktoken_import_failure(self):
        """When tiktoken is unavailable, return None."""
        # Reset global state

        tokens_module._encoder = None
        tokens_module._tiktoken_available = None

        with patch.dict("sys.modules", {"tiktoken": None}):
            with patch("builtins.__import__", side_effect=ImportError("No tiktoken")):
                encoder = _get_tiktoken_encoder()

                assert encoder is None
                assert tokens_module._tiktoken_available is False

    def test_tiktoken_caching(self):
        """Subsequent calls should use cached encoder."""

        # Set up cached state — and restore it, or the MagicMock leaks into
        # every later test in the process: len(mock.encode(text)) is 0, so
        # unrelated estimate_tokens assertions start failing order-dependently.
        saved = (tokens_module._encoder, tokens_module._tiktoken_available)
        try:
            mock_encoder = MagicMock()
            tokens_module._encoder = mock_encoder
            tokens_module._tiktoken_available = True

            # Should return cached encoder without attempting import
            encoder = _get_tiktoken_encoder()
            assert encoder is mock_encoder
        finally:
            tokens_module._encoder, tokens_module._tiktoken_available = saved


class TestHeuristicTokenCount:
    """Tests for _heuristic_token_count() function."""

    def test_empty_string_heuristic(self):
        """Empty string should return 0."""
        assert _heuristic_token_count("") == 0

    def test_english_prose(self):
        """English prose should use ~4 chars/token."""
        text = "The quick brown fox jumps over the lazy dog."
        tokens = _heuristic_token_count(text)
        # ~47 chars / 4 = ~11-12 tokens
        assert 10 <= tokens <= 13

    def test_code_text(self):
        """Code with high symbol density should use ~3 chars/token."""
        code = "function test() { return [1, 2, 3]; }"
        tokens = _heuristic_token_count(code)
        # ~38 chars / 3 = ~12-13 tokens (code detected due to symbols)
        assert 10 <= tokens <= 15

    def test_cjk_text(self):
        """CJK text should use ~1.5 chars/token."""
        # Chinese text: "Hello world, this is a test"
        cjk_text = "你好世界，这是一个测试"
        tokens = _heuristic_token_count(cjk_text)
        # ~13 chars / 1.5 = ~8-9 tokens
        assert 7 <= tokens <= 10

    def test_mixed_cjk_and_english(self):
        """Mixed CJK and English should be classified correctly."""
        mixed = "Hello 你好 world 世界"
        tokens = _heuristic_token_count(mixed)
        # Should be classified based on sample (flexible range)
        assert tokens >= 3

    def test_short_text_sample(self):
        """Text shorter than 500 chars should use entire text as sample."""
        short_text = "Short text"
        tokens = _heuristic_token_count(short_text)
        # ~10 chars / 4 = ~2-3 tokens
        assert 2 <= tokens <= 4

    def test_long_text_sampling(self):
        """Text longer than 500 chars should sample first 500."""
        # Create text with code symbols at start (will be sampled)
        long_code = "{" * 100 + "}" * 100 + ";" * 300 + "A" * 9500
        tokens = _heuristic_token_count(long_code)
        # First 500 chars are heavy code, so detected as code (~3 chars/token)
        # Total: 10000 / 3 = ~3333 tokens
        assert 3000 <= tokens <= 3700

    def test_minimum_token_guarantee(self):
        """Non-empty text should always return at least 1 token."""
        assert _heuristic_token_count("x") >= 1
        assert _heuristic_token_count("!") >= 1


class TestClassifyText:
    """Tests for _classify_text() function."""

    def test_cjk_dominated_text(self):
        """Text with >30% CJK characters should return 1.5."""
        # Simulate high CJK ratio
        chars_per_token = _classify_text(hash("test"), code_ratio=0.0, cjk_ratio=0.5)
        assert chars_per_token == 1.5

    def test_cjk_threshold_boundary(self):
        """Test CJK threshold at exactly 30%."""
        # Just above threshold
        chars_per_token = _classify_text(hash("test1"), code_ratio=0.0, cjk_ratio=0.31)
        assert chars_per_token == 1.5

        # Just below threshold (should fall to prose)
        chars_per_token = _classify_text(hash("test2"), code_ratio=0.0, cjk_ratio=0.29)
        assert chars_per_token == 4.0

    def test_code_dominated_text(self):
        """Text with >5% code indicators should return 3.0."""
        chars_per_token = _classify_text(hash("test"), code_ratio=0.1, cjk_ratio=0.0)
        assert chars_per_token == 3.0

    def test_code_threshold_boundary(self):
        """Test code threshold at exactly 5%."""
        # Just above threshold
        chars_per_token = _classify_text(hash("test3"), code_ratio=0.06, cjk_ratio=0.0)
        assert chars_per_token == 3.0

        # Just below threshold (should fall to prose)
        chars_per_token = _classify_text(hash("test4"), code_ratio=0.04, cjk_ratio=0.0)
        assert chars_per_token == 4.0

    def test_prose_default(self):
        """Normal prose with low CJK and code ratios should return 4.0."""
        chars_per_token = _classify_text(hash("test"), code_ratio=0.01, cjk_ratio=0.05)
        assert chars_per_token == 4.0

    def test_caching_behavior(self):
        """Same hash should return cached result."""
        test_hash = hash("cache_test")

        # First call
        result1 = _classify_text(test_hash, code_ratio=0.0, cjk_ratio=0.0)

        # Second call with same hash (should be cached)
        result2 = _classify_text(test_hash, code_ratio=0.0, cjk_ratio=0.0)

        assert result1 == result2 == 4.0

        # Cache info should show hits
        cache_info = _classify_text.cache_info()
        assert cache_info.hits > 0


class TestEdgeCases:
    """Tests for edge cases and special scenarios."""

    @patch("core.utils.tokens._get_tiktoken_encoder")
    def test_unicode_characters(self, mock_get_encoder):
        """Various unicode characters should be handled correctly."""
        # Force heuristic to test properly
        mock_get_encoder.return_value = None
        unicode_text = "Café résumé naïve"
        tokens = estimate_tokens(unicode_text)
        assert tokens > 0

    @patch("core.utils.tokens._get_tiktoken_encoder")
    def test_emoji_text(self, mock_get_encoder):
        """Text with emojis should be estimated."""
        # Force heuristic to test properly
        mock_get_encoder.return_value = None
        emoji_text = "Hello 👋 World 🌍"
        tokens = estimate_tokens(emoji_text)
        assert tokens > 0

    @patch("core.utils.tokens._get_tiktoken_encoder")
    def test_whitespace_only(self, mock_get_encoder):
        """Whitespace-only text should still return tokens."""
        # Force heuristic to test properly
        mock_get_encoder.return_value = None
        whitespace = "   \n\t   "
        tokens = estimate_tokens(whitespace)
        assert tokens >= 1

    @patch("core.utils.tokens._get_tiktoken_encoder")
    def test_special_characters(self, mock_get_encoder):
        """Text with special characters should be handled."""
        # Force heuristic to test properly
        mock_get_encoder.return_value = None
        special = "!@#$%^&*()_+-=[]{}|;:',.<>?/"
        tokens = estimate_tokens(special)
        # Should be classified as code due to high symbol density
        assert tokens > 0

    @patch("core.utils.tokens._get_tiktoken_encoder")
    def test_newlines_and_formatting(self, mock_get_encoder):
        """Text with newlines and formatting should be counted."""
        # Force heuristic to test properly
        mock_get_encoder.return_value = None
        formatted = "Line 1\n\nLine 2\n\tIndented\n    More indent"
        tokens = estimate_tokens(formatted)
        assert tokens >= 5

    @patch("core.utils.tokens._get_tiktoken_encoder")
    def test_very_long_single_word(self, mock_get_encoder):
        """Very long single word should be estimated."""
        # Force heuristic to test properly
        mock_get_encoder.return_value = None
        long_word = "supercalifragilisticexpialidocious" * 100
        tokens = estimate_tokens(long_word)
        # ~3400 chars, reasonable range
        assert 500 <= tokens <= 1200


class TestEstimateTokensAsync:
    @pytest.mark.asyncio
    async def test_small_text_matches_sync_inline(self):
        text = "hello world, this is a test"
        assert await estimate_tokens_async(text) == estimate_tokens(text)

    @pytest.mark.asyncio
    async def test_large_text_offloaded_and_identical(self):
        text = "word " * 20_000  # 100k chars, over the 64k threshold
        expected = tokens_module.estimate_tokens(text)

        with patch(
            "core.utils.tokens.asyncio.to_thread", wraps=tokens_module.asyncio.to_thread
        ) as spy:
            result = await tokens_module.estimate_tokens_async(text)
        spy.assert_awaited_once()
        assert result == expected

    @pytest.mark.asyncio
    async def test_empty_text(self):
        assert await estimate_tokens_async("") == 0


class TestModelCalibration:
    """Per-family tokenizer calibration on top of the cl100k baseline."""

    def test_claude_models_are_calibrated_up(self):
        text = "The quick brown fox jumps over the lazy dog. " * 50
        baseline = estimate_tokens(text)
        claude = estimate_tokens(text, model="claude-sonnet-5")
        # Claude tokenizes ~15-20% denser than cl100k; the estimate must not
        # silently under-budget truncation for Claude models.
        assert claude == max(1, round(baseline * 1.2))

    def test_openai_and_unknown_models_unchanged(self):
        text = "hello world, this is a calibration check"
        assert estimate_tokens(text, model="gpt-4o") == estimate_tokens(text)
        assert estimate_tokens(text, model=None) == estimate_tokens(text)
