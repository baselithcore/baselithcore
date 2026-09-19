"""What the fallback chain must and must not absorb, and what it must say.

Three properties, each one a production failure the chain used to cause rather
than prevent:

1. A **rejected request** (expired key, unknown model, malformed payload) is
   not an outage. Falling through turns a lapsed hosted credential into
   permanent local inference that answers every request successfully, so the
   deployment looks healthy while running on a model nobody chose.
2. A fallback that serves is **counted**, not merely logged, because the
   request returns 200 either way and a log line is not an alert.
3. A chain that fails entirely reports **every** stage's error. The streaming
   path used to report only the last, which named the local fallback as the
   cause of an outage that started at the hosted primary.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.services.llm.errors import LLMClientError, LLMServerError
from core.services.llm.exceptions import LLMProviderError
from core.services.llm.fallback_runtime import (
    maybe_run_messages_with_fallback,
    maybe_run_structured_with_fallback,
    run_with_fallback,
)


def _service(fallback_chain="openai:gpt-4o-mini"):
    from core.config.services import LLMConfig
    from core.services.llm.service import LLMService

    config = LLMConfig(
        provider="ollama", model="llama3.2", fallback_chain=fallback_chain
    )
    with patch.object(LLMService, "_create_provider", return_value=AsyncMock()):
        return LLMService(config=config, enable_cache=False)


@pytest.mark.asyncio
class TestClientErrorIsFatal:
    """A misconfiguration must not be answered by a different provider."""

    async def test_text_path_does_not_fall_through_on_client_error(self):
        service = _service()
        clone = AsyncMock()
        clone._generate_with_retry = AsyncMock(return_value=("local answer", 3))
        with (
            patch.object(
                service,
                "_generate_with_retry",
                AsyncMock(
                    side_effect=LLMClientError("invalid api key", status_code=401)
                ),
            ),
            patch(
                # The name the chain actually calls: ``fallback_runtime``
                # imported it, so patching the definition module is a no-op.
                "core.services.llm.fallback_runtime._clone_service",
                return_value=clone,
            ),
        ):
            with pytest.raises(LLMClientError):
                await run_with_fallback(
                    service, prompt="p", model="llama3.2", json_mode=False
                )
        # The whole point: the local stage was never asked, so the deployment
        # sees the 401 instead of a plausible answer from a model it did not
        # choose.
        clone._generate_with_retry.assert_not_awaited()

    async def test_server_error_still_falls_through(self):
        """The contrast case: a 5xx IS an outage, and failover is the answer."""
        service = _service()
        clone = AsyncMock()
        clone._generate_with_retry = AsyncMock(return_value=("saved", 3))
        with (
            patch.object(
                service,
                "_generate_with_retry",
                AsyncMock(side_effect=LLMServerError("upstream 503", status_code=503)),
            ),
            patch(
                # The name the chain actually calls: ``fallback_runtime``
                # imported it, so patching the definition module is a no-op.
                "core.services.llm.fallback_runtime._clone_service",
                return_value=clone,
            ),
        ):
            content, _tokens, provider, model = await run_with_fallback(
                service, prompt="p", model="llama3.2", json_mode=False
            )
        assert (content, provider, model) == ("saved", "openai", "gpt-4o-mini")

    async def test_structured_path_does_not_fall_through_on_client_error(self):
        service = _service()
        with patch(
            "core.services.llm.structured._native_with_retry",
            AsyncMock(side_effect=LLMClientError("unknown model", status_code=404)),
        ):
            with pytest.raises(LLMClientError):
                await maybe_run_structured_with_fallback(service, "p", "llama3.2")

    async def test_messages_path_does_not_fall_through_on_client_error(self):
        service = _service()
        with patch(
            "core.services.llm.message_runtime._messages_with_retry",
            AsyncMock(side_effect=LLMClientError("bad request", status_code=400)),
        ):
            with pytest.raises(LLMClientError):
                await maybe_run_messages_with_fallback(service, [], "llama3.2")


@pytest.mark.asyncio
class TestFallbackIsCounted:
    async def test_serving_stage_increments_the_counter(self):
        from core.services.llm._fallback_support import record_fallback_served

        with patch("core.observability.metrics.LLM_FALLBACK_SERVED_TOTAL") as counter:
            record_fallback_served(
                primary="openai",
                served_by="ollama",
                served_model="llama3.2",
                path="text",
            )
        counter.labels.assert_called_once_with("openai", "ollama", "text")
        counter.labels.return_value.inc.assert_called_once()

    async def test_a_primary_answer_is_not_counted(self):
        service = _service()
        with (
            patch.object(
                service, "_generate_with_retry", AsyncMock(return_value=("hi", 1))
            ),
            patch(
                "core.services.llm._fallback_support.record_fallback_served"
            ) as recorded,
        ):
            await run_with_fallback(
                service, prompt="p", model="llama3.2", json_mode=False
            )
        recorded.assert_not_called()

    async def test_metrics_failure_never_breaks_the_request(self):
        from core.services.llm._fallback_support import record_fallback_served

        with patch("core.observability.metrics.LLM_FALLBACK_SERVED_TOTAL") as counter:
            counter.labels.side_effect = RuntimeError("registry exploded")
            record_fallback_served(
                primary="openai", served_by="ollama", served_model="m", path="text"
            )


@pytest.mark.asyncio
class TestEveryFailureIsReported:
    async def test_buffered_error_names_each_stage(self):
        service = _service()
        clone = AsyncMock()
        clone._generate_with_retry = AsyncMock(
            side_effect=LLMProviderError("Ollama error: Connection refused")
        )
        with (
            patch.object(
                service,
                "_generate_with_retry",
                AsyncMock(side_effect=LLMServerError("upstream 503", status_code=503)),
            ),
            patch(
                # The name the chain actually calls: ``fallback_runtime``
                # imported it, so patching the definition module is a no-op.
                "core.services.llm.fallback_runtime._clone_service",
                return_value=clone,
            ),
        ):
            with pytest.raises(LLMProviderError) as excinfo:
                await run_with_fallback(
                    service, prompt="p", model="llama3.2", json_mode=False
                )
        message = str(excinfo.value)
        assert "503" in message and "Connection refused" in message

    async def test_streaming_error_names_each_stage(self):
        """The regression: the local stage's refusal used to be the only clue."""
        from core.services.llm._stream_fallback import open_stream

        service = _service()

        async def _broken_primary(*_args, **_kwargs):
            raise LLMServerError("upstream 503", status_code=503)
            yield  # pragma: no cover - makes this an async generator

        async def _broken_fallback(*_args, **_kwargs):
            raise LLMProviderError("Ollama error: Connection refused")
            yield  # pragma: no cover

        service.provider = Mock()
        service.provider.generate_stream = _broken_primary
        clone = Mock()
        clone.provider = Mock()
        clone.provider.generate_stream = _broken_fallback

        with patch(
            "core.services.llm._stream_fallback._clone_service", return_value=clone
        ):
            with pytest.raises(LLMProviderError) as excinfo:
                await open_stream(service, "p", "llama3.2", {})
        message = str(excinfo.value)
        assert "503" in message, message
        assert "Connection refused" in message, message
