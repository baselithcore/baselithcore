"""Unit tests for the neutral LLM error taxonomy and SDK exception mapping."""

import pytest

from core.services.llm.errors import (
    LLMClientError,
    LLMConnectionError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMServerError,
    LLMTimeoutError,
    is_retryable,
    map_provider_exception,
    retry_after_from_exception,
)
from core.services.llm.exceptions import LLMProviderError, RateLimitError


class _Headers(dict):
    """Minimal ``httpx.Headers`` stand-in (case-insensitive ``get``)."""

    def get(self, key, default=None):
        for name, value in self.items():
            if name.lower() == key.lower():
                return value
        return default


class _Response:
    def __init__(self, headers=None):
        self.headers = _Headers(headers or {})


# Stand-ins mirroring the shape both SDKs share (anthropic and openai declare
# structurally identical hierarchies): APIStatusError carries ``status_code``,
# APITimeoutError subclasses APIConnectionError.
class APIError(Exception):
    pass


class APIStatusError(APIError):
    def __init__(self, message, status_code, headers=None):
        super().__init__(message)
        self.status_code = status_code
        self.response = _Response(headers)


class APIConnectionError(APIError):
    pass


class APITimeoutError(APIConnectionError):
    pass


class SDKRateLimitError(APIStatusError):
    def __init__(self, message="rate limited", headers=None):
        super().__init__(message, 429, headers)


class TestErrorTaxonomy:
    def test_every_neutral_error_is_a_provider_error(self):
        for cls in (
            LLMRateLimitError,
            LLMServerError,
            LLMConnectionError,
            LLMTimeoutError,
            LLMClientError,
            LLMRefusalError,
        ):
            assert issubclass(cls, LLMProviderError)

    def test_rate_limit_stays_compatible_with_the_legacy_class(self):
        # The retry layer is armed with the historical RateLimitError; the new
        # class must keep triggering it.
        assert issubclass(LLMRateLimitError, RateLimitError)
        assert LLMRateLimitError("x", retry_after=3.0).retry_after == 3.0

    def test_refusal_carries_category_and_explanation(self):
        err = LLMRefusalError(category="safety", explanation="nope")
        assert err.category == "safety"
        assert err.explanation == "nope"
        assert "safety" in str(err)


class TestRetryEligibility:
    @pytest.mark.parametrize(
        "exc",
        [
            LLMRateLimitError("429"),
            LLMServerError("boom", status_code=503),
            LLMConnectionError("dns"),
            LLMTimeoutError("slow"),
        ],
    )
    def test_transient_classes_are_retryable(self, exc):
        assert is_retryable(exc) is True

    @pytest.mark.parametrize(
        "exc",
        [
            LLMClientError("bad request", status_code=400),
            LLMRefusalError(category="safety", explanation="no"),
            LLMProviderError("something structural"),
            ValueError("unrelated"),
        ],
    )
    def test_permanent_failures_are_not_retryable(self, exc):
        assert is_retryable(exc) is False

    def test_unknown_exception_still_matches_on_text(self):
        # String fallback only for types the taxonomy does not cover.
        assert is_retryable(RuntimeError("HTTP 429 Too Many Requests")) is True
        assert is_retryable(RuntimeError("rate limit exceeded")) is True
        assert is_retryable(RuntimeError("invalid api key")) is False


class TestExceptionMapping:
    def test_timeout_maps_before_connection(self):
        mapped = map_provider_exception(APITimeoutError("timed out"), provider="Test")
        assert isinstance(mapped, LLMTimeoutError)
        assert "Test error" in str(mapped)

    def test_connection_error_maps(self):
        mapped = map_provider_exception(APIConnectionError("no route"), provider="Test")
        assert isinstance(mapped, LLMConnectionError)
        assert not isinstance(mapped, LLMTimeoutError)

    def test_429_maps_to_rate_limit_with_retry_after(self):
        exc = SDKRateLimitError(headers={"Retry-After": "7"})
        mapped = map_provider_exception(exc, provider="Test")
        assert isinstance(mapped, LLMRateLimitError)
        assert mapped.retry_after == 7.0

    def test_5xx_maps_to_server_error(self):
        mapped = map_provider_exception(
            APIStatusError("upstream exploded", 503), provider="Test"
        )
        assert isinstance(mapped, LLMServerError)
        assert mapped.status_code == 503

    def test_4xx_maps_to_client_error(self):
        mapped = map_provider_exception(
            APIStatusError("bad request", 400), provider="Test"
        )
        assert isinstance(mapped, LLMClientError)
        assert mapped.status_code == 400
        assert is_retryable(mapped) is False

    def test_builtin_timeout_maps_to_timeout(self):
        assert isinstance(
            map_provider_exception(TimeoutError(), provider="Test"), LLMTimeoutError
        )

    def test_unknown_exception_becomes_a_plain_provider_error(self):
        mapped = map_provider_exception(ValueError("weird"), provider="Test")
        assert type(mapped) is LLMProviderError
        assert "Test error: weird" in str(mapped)

    def test_message_falls_back_to_the_type_name(self):
        mapped = map_provider_exception(APITimeoutError(), provider="Test")
        assert "APITimeoutError" in str(mapped)

    def test_already_neutral_errors_pass_through_unchanged(self):
        original = LLMRefusalError(category="safety", explanation="no")
        assert map_provider_exception(original, provider="Test") is original

    def test_custom_message_prefix_is_honoured(self):
        mapped = map_provider_exception(
            APIConnectionError("down"), provider="Test", action="streaming"
        )
        assert "Test streaming error: down" in str(mapped)


class TestRetryAfterParsing:
    def test_reads_delta_seconds(self):
        exc = SDKRateLimitError(headers={"retry-after": "12"})
        assert retry_after_from_exception(exc) == 12.0

    def test_ignores_absurd_windows(self):
        exc = SDKRateLimitError(headers={"retry-after": "100000"})
        assert retry_after_from_exception(exc) is None

    def test_ignores_http_date_form(self):
        exc = SDKRateLimitError(
            headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}
        )
        assert retry_after_from_exception(exc) is None

    def test_no_response_is_none(self):
        assert retry_after_from_exception(Exception("plain")) is None
