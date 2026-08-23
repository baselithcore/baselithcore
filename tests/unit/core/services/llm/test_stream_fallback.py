"""Unit tests for cross-provider failover on the streaming path.

The regression these cover: with an unreachable primary provider, buffered
calls fell through `fallback_chain` while every streaming surface died.
"""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from core.services.llm._stream_fallback import open_stream
from core.services.llm.exceptions import LLMProviderError


def _make_service(provider="ollama", fallback_chain="openai:gpt-4o-mini"):
    """LLMService with a mocked provider and a configured fallback chain."""
    from core.config.services import LLMConfig
    from core.services.llm.service import LLMService

    config = LLMConfig(
        provider=provider, model="llama3.2", fallback_chain=fallback_chain
    )
    with patch.object(LLMService, "_create_provider", return_value=AsyncMock()):
        return LLMService(config=config, enable_cache=False)


def _chunks(*items: str):
    """A provider `generate_stream` yielding (chunk, cumulative_tokens)."""

    async def _stream(**_kwargs) -> AsyncIterator[tuple[str, int]]:
        total = 0
        for item in items:
            total += len(item)
            yield item, total

    return _stream


def _failing(exc: Exception):
    """A provider `generate_stream` that dies before the first chunk."""

    async def _stream(**_kwargs) -> AsyncIterator[tuple[str, int]]:
        raise exc
        yield  # pragma: no cover — unreachable

    return _stream


async def _collect(stream) -> list[str]:
    return [chunk async for chunk, _tokens in stream]


class TestOpenStream:
    async def test_primary_serves_when_healthy(self):
        service = _make_service()
        service.provider.generate_stream = _chunks("he", "llo")

        stream, serving, provider, model = await open_stream(service, "p", "llama3.2", {})

        assert await _collect(stream) == ["he", "llo"]
        assert provider == "ollama" and model == "llama3.2"
        assert serving is service

    async def test_falls_over_when_primary_cannot_connect(self):
        """The exact production failure: Ollama down, chat must still answer."""
        service = _make_service()
        service.provider.generate_stream = _failing(
            ConnectionError("All connection attempts failed")
        )
        secondary = _make_service(provider="openai", fallback_chain="")
        secondary.provider.generate_stream = _chunks("saved")

        with patch(
            "core.services.llm._stream_fallback._clone_service", return_value=secondary
        ):
            stream, serving, provider, model = await open_stream(
                service, "p", "llama3.2", {}
            )

        assert await _collect(stream) == ["saved"]
        assert provider == "openai" and model == "gpt-4o-mini"
        assert serving is secondary

    async def test_budget_errors_never_fall_through(self):
        """A request out of budget must not spend more of it on a second provider."""
        from core.services.llm.exceptions import BudgetExceededError

        service = _make_service()
        service.provider.generate_stream = _failing(BudgetExceededError("cap"))

        with patch(
            "core.services.llm._stream_fallback._clone_service"
        ) as clone, pytest.raises(BudgetExceededError):
            await open_stream(service, "p", "llama3.2", {})
        clone.assert_not_called()

    async def test_raises_when_every_provider_fails(self):
        service = _make_service()
        service.provider.generate_stream = _failing(ConnectionError("primary down"))
        secondary = _make_service(provider="openai", fallback_chain="")
        secondary.provider.generate_stream = _failing(ConnectionError("secondary down"))

        with patch(
            "core.services.llm._stream_fallback._clone_service", return_value=secondary
        ), pytest.raises(LLMProviderError, match="secondary down"):
            await open_stream(service, "p", "llama3.2", {})

    async def test_empty_stream_is_not_a_failure(self):
        service = _make_service()
        service.provider.generate_stream = _chunks()

        stream, _serving, provider, _model = await open_stream(
            service, "p", "llama3.2", {}
        )

        assert await _collect(stream) == []
        assert provider == "ollama"

    async def test_open_breaker_skips_the_primary(self):
        service = _make_service()
        service.provider.generate_stream = _chunks("never")
        secondary = _make_service(provider="openai", fallback_chain="")
        secondary.provider.generate_stream = _chunks("secondary")

        with patch(
            "core.services.llm._stream_fallback._breaker_open",
            side_effect=lambda name: name == "ollama",
        ), patch(
            "core.services.llm._stream_fallback._clone_service", return_value=secondary
        ):
            stream, _serving, provider, _model = await open_stream(
                service, "p", "llama3.2", {}
            )

        assert await _collect(stream) == ["secondary"]
        assert provider == "openai"

    async def test_no_chain_configured_means_no_fallback(self):
        service = _make_service(fallback_chain="")
        service.provider.generate_stream = _failing(ConnectionError("down"))

        with patch(
            "core.services.llm._stream_fallback._clone_service"
        ) as clone, pytest.raises(LLMProviderError, match="down"):
            await open_stream(service, "p", "llama3.2", {})
        clone.assert_not_called()
