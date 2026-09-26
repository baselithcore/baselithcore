"""Unit tests for exact Claude token counting in ``core.utils.tokens``.

``estimate_tokens_async`` prefers
``anthropic.Anthropic().messages.count_tokens`` for ``claude*`` models when
the SDK is installed, ``ANTHROPIC_API_KEY`` is set, AND the opt-in
``BASELITH_EXACT_TOKEN_COUNTING`` setting is enabled — off by default, since
an API key alone is the common case in production and must not silently
turn every token estimate into a blocking network call.

The sync ``estimate_tokens`` NEVER performs network I/O, even when exact
counting is available: several call sites (streaming provider loops) call it
synchronously from a hot path, and a blocking HTTP request there would stall
the event loop per delta. Only ``estimate_tokens_async`` may reach the
network, and it always does so via ``asyncio.to_thread``.

All network calls are mocked; availability gating is exercised against the
real installed SDK (construction alone never hits the network).
"""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import core.utils.tokens as tokens_module

count_tokens_exact_available = tokens_module.count_tokens_exact_available
estimate_tokens = tokens_module.estimate_tokens
estimate_tokens_async = tokens_module.estimate_tokens_async


@pytest.fixture(autouse=True)
def _reset_exact_token_globals(monkeypatch):
    """Every test starts with a clean, unmemoized loader/config/cache state."""
    monkeypatch.setattr(tokens_module, "_anthropic_client", None)
    monkeypatch.setattr(tokens_module, "_anthropic_client_checked", False)
    monkeypatch.setattr(tokens_module, "_exact_token_counting_config", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BASELITH_EXACT_TOKEN_COUNTING", raising=False)
    # The key is resolved through the cached LLMConfig: rebuild it per test.
    monkeypatch.setattr("core.config.services._llm_config", None)
    tokens_module._exact_token_cache.clear()
    tokens_module._exact_count_failed_at.clear()
    yield
    tokens_module._exact_token_cache.clear()
    tokens_module._exact_count_failed_at.clear()


def _enable_exact_counting(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("BASELITH_EXACT_TOKEN_COUNTING", "true")


def _mock_client(input_tokens: int = 7) -> MagicMock:
    client = MagicMock()
    client.messages.count_tokens.return_value = SimpleNamespace(
        input_tokens=input_tokens
    )
    return client


class TestAvailabilityGating:
    def test_unavailable_without_an_api_key(self, monkeypatch):
        monkeypatch.setenv("BASELITH_EXACT_TOKEN_COUNTING", "true")
        assert count_tokens_exact_available() is False

    def test_unavailable_when_setting_disabled_even_with_a_key(self, monkeypatch):
        """The old (implicit) behaviour: an API key alone is NOT enough."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
        assert count_tokens_exact_available() is False

    def test_available_with_sdk_key_and_setting_enabled(self, monkeypatch):
        _enable_exact_counting(monkeypatch)
        assert count_tokens_exact_available() is True

    def test_llm_prefixed_key_is_honoured(self, monkeypatch):
        """``LLM_ANTHROPIC_API_KEY`` binds the same field as the bare name."""
        monkeypatch.setenv("LLM_ANTHROPIC_API_KEY", "sk-test-not-real")
        monkeypatch.setenv("BASELITH_EXACT_TOKEN_COUNTING", "true")
        client = count_tokens_exact_available() and tokens_module._anthropic_client
        assert client
        assert client.api_key == "sk-test-not-real"

    def test_blank_key_counts_as_unset(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
        monkeypatch.setenv("BASELITH_EXACT_TOKEN_COUNTING", "true")
        assert count_tokens_exact_available() is False

    def test_unavailable_when_sdk_not_importable(self, monkeypatch):
        _enable_exact_counting(monkeypatch)
        with patch.dict(sys.modules, {"anthropic": None}):
            assert count_tokens_exact_available() is False

    def test_result_is_memoized_across_calls(self, monkeypatch):
        _enable_exact_counting(monkeypatch)
        assert count_tokens_exact_available() is True
        # Removing the key afterwards must not flip a memoized process-lifetime
        # decision — the loader only checks once.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert count_tokens_exact_available() is True


class TestSyncNeverHitsTheNetwork:
    """core finding: estimate_tokens (sync) must never perform network I/O,
    even when exact counting is fully available."""

    def test_estimate_tokens_never_calls_the_sdk(self, monkeypatch):
        _enable_exact_counting(monkeypatch)
        client = _mock_client(999)
        monkeypatch.setattr(tokens_module, "_load_anthropic_client", lambda: client)

        text = "The quick brown fox jumps over the lazy dog. " * 10
        heuristic_equivalent = tokens_module._heuristic_token_count(text)
        result = estimate_tokens(text, model="claude-sonnet-5")

        client.messages.count_tokens.assert_not_called()
        assert result == max(1, round(heuristic_equivalent * 1.2))

    def test_estimate_tokens_ignores_available_exact_counting_entirely(
        self, monkeypatch
    ):
        _enable_exact_counting(monkeypatch)
        # Even a real (memoized) available client must not be consulted.
        assert count_tokens_exact_available() is True
        with patch.object(
            tokens_module,
            "_count_tokens_exact",
            wraps=tokens_module._count_tokens_exact,
        ) as spy:
            estimate_tokens("hello there", model="claude-sonnet-5")
            spy.assert_not_called()


class TestAsyncExactCountingPath:
    """Only estimate_tokens_async may call the network, always thread-offloaded."""

    @pytest.mark.asyncio
    async def test_uses_exact_count_when_enabled(self, monkeypatch):
        _enable_exact_counting(monkeypatch)
        monkeypatch.setattr(
            tokens_module, "_load_anthropic_client", lambda: _mock_client(7)
        )
        assert await estimate_tokens_async("hello there", model="claude-sonnet-5") == 7

    @pytest.mark.asyncio
    async def test_disabled_setting_falls_back_to_heuristic(self, monkeypatch):
        # API key present, but BASELITH_EXACT_TOKEN_COUNTING not set: must not
        # use the mocked client at all.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
        client = _mock_client(999)
        monkeypatch.setattr(tokens_module, "_load_anthropic_client", lambda: client)
        text = "plain text"
        result = await estimate_tokens_async(text, model="claude-sonnet-5")
        client.messages.count_tokens.assert_not_called()
        assert result == estimate_tokens(text, model="claude-sonnet-5")

    @pytest.mark.asyncio
    async def test_non_claude_model_never_calls_the_sdk(self, monkeypatch):
        _enable_exact_counting(monkeypatch)
        client = _mock_client(999)
        monkeypatch.setattr(tokens_module, "_load_anthropic_client", lambda: client)
        await estimate_tokens_async("hello there", model="gpt-4o")
        client.messages.count_tokens.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_heuristic_when_sdk_call_raises(self, monkeypatch):
        _enable_exact_counting(monkeypatch)
        client = MagicMock()
        client.messages.count_tokens.side_effect = RuntimeError("boom")
        monkeypatch.setattr(tokens_module, "_load_anthropic_client", lambda: client)
        text = "The quick brown fox jumps over the lazy dog. " * 10
        heuristic_equivalent = tokens_module._heuristic_token_count(text)
        result = await estimate_tokens_async(text, model="claude-sonnet-5")
        assert result == max(1, round(heuristic_equivalent * 1.2))

    @pytest.mark.asyncio
    async def test_exact_capable_small_text_is_thread_offloaded(self, monkeypatch):
        """A network-backed exact count must never block the event loop, even
        for tiny texts that the pure-CPU fast-path would normally inline."""
        _enable_exact_counting(monkeypatch)
        monkeypatch.setattr(
            tokens_module, "_load_anthropic_client", lambda: _mock_client(4)
        )
        with patch(
            "core.utils.tokens.asyncio.to_thread",
            wraps=tokens_module.asyncio.to_thread,
        ) as spy:
            result = await estimate_tokens_async("hi", model="claude-sonnet-5")
        spy.assert_awaited_once()
        assert result == 4

    @pytest.mark.asyncio
    async def test_non_claude_small_text_stays_inline(self, monkeypatch):
        _enable_exact_counting(monkeypatch)
        with patch(
            "core.utils.tokens.asyncio.to_thread",
            wraps=tokens_module.asyncio.to_thread,
        ) as spy:
            await estimate_tokens_async("hi", model="gpt-4o")
        spy.assert_not_awaited()


class TestExactCountCache:
    """_count_tokens_exact's own cache mechanics — invoked directly since the
    caching logic is independent of whether the sync or async wrapper calls it."""

    def test_repeat_calls_hit_the_cache_not_the_sdk(self, monkeypatch):
        _enable_exact_counting(monkeypatch)
        client = _mock_client(11)
        monkeypatch.setattr(tokens_module, "_load_anthropic_client", lambda: client)
        text = "cache me please"
        assert tokens_module._count_tokens_exact(text, "claude-opus-5") == 11
        assert tokens_module._count_tokens_exact(text, "claude-opus-5") == 11
        client.messages.count_tokens.assert_called_once()

    def test_cache_is_keyed_by_model_and_text(self, monkeypatch):
        _enable_exact_counting(monkeypatch)
        client = _mock_client(11)
        monkeypatch.setattr(tokens_module, "_load_anthropic_client", lambda: client)
        text = "same text, different model"
        tokens_module._count_tokens_exact(text, "claude-opus-5")
        tokens_module._count_tokens_exact(text, "claude-haiku-4-5")
        assert client.messages.count_tokens.call_count == 2

    def test_cache_never_exceeds_its_configured_cap(self, monkeypatch):
        _enable_exact_counting(monkeypatch)
        client = _mock_client(3)
        monkeypatch.setattr(tokens_module, "_load_anthropic_client", lambda: client)
        cap = tokens_module._EXACT_TOKEN_CACHE_MAX_ENTRIES
        for i in range(cap + 25):
            tokens_module._count_tokens_exact(f"unique text #{i}", "claude-opus-5")
        assert len(tokens_module._exact_token_cache) == cap


class TestNegativeResultCaching:
    """A model id that fails must not pay timeout+retries on every call."""

    def test_repeated_failures_are_not_retried_within_the_ttl(self, monkeypatch):
        _enable_exact_counting(monkeypatch)
        client = MagicMock()
        client.messages.count_tokens.side_effect = RuntimeError("boom")
        monkeypatch.setattr(tokens_module, "_load_anthropic_client", lambda: client)

        assert tokens_module._count_tokens_exact("text one", "claude-opus-5") is None
        assert tokens_module._count_tokens_exact("text two", "claude-opus-5") is None
        client.messages.count_tokens.assert_called_once()

    def test_failure_cache_is_keyed_per_model(self, monkeypatch):
        _enable_exact_counting(monkeypatch)
        client = MagicMock()
        client.messages.count_tokens.side_effect = RuntimeError("boom")
        monkeypatch.setattr(tokens_module, "_load_anthropic_client", lambda: client)

        tokens_module._count_tokens_exact("text", "claude-opus-5")
        tokens_module._count_tokens_exact("text", "claude-haiku-4-5")
        assert client.messages.count_tokens.call_count == 2

    def test_failure_is_retried_after_the_ttl_expires(self, monkeypatch):
        _enable_exact_counting(monkeypatch)
        client = MagicMock()
        client.messages.count_tokens.side_effect = RuntimeError("boom")
        monkeypatch.setattr(tokens_module, "_load_anthropic_client", lambda: client)

        assert tokens_module._count_tokens_exact("text one", "claude-opus-5") is None
        # Simulate the TTL having elapsed.
        monkeypatch.setattr(
            tokens_module,
            "_exact_count_failed_at",
            {
                "claude-opus-5": time.monotonic()
                - tokens_module._EXACT_COUNT_FAILURE_TTL_SECONDS
                - 1
            },
        )
        client.messages.count_tokens.side_effect = None
        client.messages.count_tokens.return_value = SimpleNamespace(input_tokens=42)
        assert tokens_module._count_tokens_exact("text two", "claude-opus-5") == 42
