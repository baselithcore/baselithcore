"""
Unit tests for LLM service model selection.

Covers model-routing resolution, fallback-chain wiring and thinking-effort
propagation, all with mocked providers.
"""

from unittest.mock import Mock, patch

import pytest

from core.services.llm import LLMService


class TestModelRoutingResolution:
    def _service(self, **config_kwargs):
        from unittest.mock import AsyncMock

        from core.config.services import LLMConfig

        config = LLMConfig(provider="ollama", model="llama3.2", **config_kwargs)
        with patch.object(LLMService, "_create_provider", return_value=AsyncMock()):
            return LLMService(config=config, enable_cache=False)

    def test_routing_disabled_ignores_category(self):
        service = self._service(routing_enabled=False)
        assert service._resolve_model(None, task_category="planning") == "llama3.2"

    def test_routing_selects_policy_model(self):
        service = self._service(
            routing_enabled=True,
            routing_policy='{"planning": "big-model", "classification": "small-model"}',
        )
        assert service._resolve_model(None, task_category="planning") == "big-model"
        assert (
            service._resolve_model(None, task_category="classification")
            == "small-model"
        )

    def test_explicit_model_beats_routing(self):
        service = self._service(
            routing_enabled=True, routing_policy='{"planning": "big-model"}'
        )
        assert (
            service._resolve_model("pinned-call", task_category="planning")
            == "pinned-call"
        )

    def test_unknown_category_falls_back_to_config_model(self):
        service = self._service(routing_enabled=True, routing_policy="{}")
        assert service._resolve_model(None, task_category="nonsense") == "llama3.2"


class TestFallbackWiring:
    @pytest.mark.asyncio
    async def test_generate_response_uses_fallback_runtime_when_configured(self):
        from unittest.mock import AsyncMock

        from core.config.services import LLMConfig

        config = LLMConfig(
            provider="ollama", model="llama3.2", fallback_chain="openai:gpt-4o-mini"
        )
        with patch.object(LLMService, "_create_provider", return_value=AsyncMock()):
            service = LLMService(config=config, enable_cache=False)
        with patch(
            "core.services.llm.fallback_runtime.run_with_fallback",
            AsyncMock(return_value=("saved", 5, "openai")),
        ) as rwf:
            result = await service.generate_response("hello")
        assert result == "saved"
        rwf.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_chain_configured_keeps_direct_path(self):
        from unittest.mock import AsyncMock

        from core.config.services import LLMConfig

        config = LLMConfig(provider="ollama", model="llama3.2", fallback_chain="")
        with patch.object(LLMService, "_create_provider", return_value=AsyncMock()):
            service = LLMService(config=config, enable_cache=False)
        with (
            patch.object(
                service, "_generate_with_retry", AsyncMock(return_value=("hi", 3))
            ) as direct,
            patch(
                "core.services.llm.fallback_runtime.run_with_fallback", AsyncMock()
            ) as rwf,
        ):
            result = await service.generate_response("hello")
        assert result == "hi"
        direct.assert_awaited_once()
        rwf.assert_not_awaited()


class TestThinkingEffortPropagation:
    """generate_response derives effort from task_category when enabled."""

    def _service_with_mock_provider(self, mock_config, thinking_enabled: bool):
        from unittest.mock import AsyncMock

        mock_config.return_value = Mock(
            provider="ollama",
            model="llama3.2",
            api_base=None,
            enable_cache=False,
            cache_max_size=1000,
            cache_ttl=3600,
            fallback_chain="",
            routing_enabled=False,
            thinking_enabled=thinking_enabled,
        )
        service = LLMService()
        mock_provider = Mock()
        mock_provider.generate = AsyncMock(return_value=("ok", 10))
        service.provider = mock_provider
        service._provider_chain = [mock_provider]
        return service, mock_provider

    @pytest.mark.asyncio
    @patch("core.services.llm.service.get_llm_config")
    async def test_effort_derived_from_category_when_enabled(self, mock_config):
        service, provider = self._service_with_mock_provider(
            mock_config, thinking_enabled=True
        )
        await service.generate_response("p", task_category="planning")
        assert provider.generate.call_args.kwargs.get("effort") == "high"

    @pytest.mark.asyncio
    @patch("core.services.llm.service.get_llm_config")
    async def test_no_effort_when_disabled(self, mock_config):
        service, provider = self._service_with_mock_provider(
            mock_config, thinking_enabled=False
        )
        await service.generate_response("p", task_category="planning")
        assert "effort" not in provider.generate.call_args.kwargs

    @pytest.mark.asyncio
    @patch("core.services.llm.service.get_llm_config")
    async def test_off_category_adds_no_kwarg(self, mock_config):
        service, provider = self._service_with_mock_provider(
            mock_config, thinking_enabled=True
        )
        await service.generate_response("p", task_category="classification")
        assert "effort" not in provider.generate.call_args.kwargs

    @pytest.mark.asyncio
    @patch("core.services.llm.service.get_llm_config")
    async def test_explicit_effort_wins_over_category(self, mock_config):
        service, provider = self._service_with_mock_provider(
            mock_config, thinking_enabled=True
        )
        await service.generate_response("p", task_category="planning", effort="low")
        assert provider.generate.call_args.kwargs.get("effort") == "low"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
