"""Unit tests for the LLMService cross-provider fallback runtime."""

from unittest.mock import AsyncMock, patch

import pytest

from core.services.llm.fallback_runtime import (
    parse_fallback_chain,
    run_with_fallback,
)


class TestParseFallbackChain:
    def test_empty_spec_returns_empty_list(self):
        assert parse_fallback_chain("") == []
        assert parse_fallback_chain("   ") == []

    def test_parses_ordered_pairs(self):
        assert parse_fallback_chain("openai:gpt-4o-mini, ollama:llama3.2") == [
            ("openai", "gpt-4o-mini"),
            ("ollama", "llama3.2"),
        ]

    def test_rejects_malformed_entry(self):
        with pytest.raises(ValueError, match="provider:model"):
            parse_fallback_chain("openai")

    def test_rejects_unsupported_provider(self):
        with pytest.raises(ValueError, match="Unsupported"):
            parse_fallback_chain("bedrock:claude")


def _make_service(fallback_chain="openai:gpt-4o-mini"):
    """LLMService with mocked provider construction and a fallback chain."""
    from core.config.services import LLMConfig
    from core.services.llm.service import LLMService

    config = LLMConfig(
        provider="ollama", model="llama3.2", fallback_chain=fallback_chain
    )
    with patch.object(LLMService, "_create_provider", return_value=AsyncMock()):
        return LLMService(config=config, enable_cache=False)


class TestRunWithFallback:
    async def test_primary_success_no_fallback(self):
        service = _make_service()
        with patch.object(
            service, "_generate_with_retry", AsyncMock(return_value=("hi", 7))
        ) as primary:
            content, tokens, provider_name = await run_with_fallback(
                service, "p", model="llama3.2", json_mode=False
            )
        assert (content, tokens, provider_name) == ("hi", 7, "ollama")
        primary.assert_awaited_once()

    async def test_falls_through_to_secondary_on_primary_failure(self):
        service = _make_service()
        secondary = AsyncMock()
        secondary._generate_with_retry = AsyncMock(return_value=("saved", 3))
        with (
            patch.object(
                service,
                "_generate_with_retry",
                AsyncMock(side_effect=RuntimeError("down")),
            ),
            patch(
                "core.services.llm.fallback_runtime._clone_service",
                return_value=secondary,
            ),
        ):
            content, tokens, provider_name = await run_with_fallback(
                service, "p", model="llama3.2", json_mode=False
            )
        assert (content, tokens, provider_name) == ("saved", 3, "openai")
        # The fallback entry's model wins over the primary's model.
        assert (
            secondary._generate_with_retry.await_args.kwargs["model"] == "gpt-4o-mini"
        )

    async def test_budget_error_is_fatal_no_fallthrough(self):
        from core.orchestration.limits import BudgetExceededError, LoopBudget

        service = _make_service()
        budget = LoopBudget()
        with (
            patch.object(
                service,
                "_generate_with_retry",
                AsyncMock(
                    side_effect=BudgetExceededError("max_seconds", budget.snapshot())
                ),
            ),
            patch("core.services.llm.fallback_runtime._clone_service") as clone,
        ):
            with pytest.raises(BudgetExceededError):
                await run_with_fallback(service, "p", model="llama3.2", json_mode=False)
        clone.assert_not_called()

    async def test_all_failed_raises_llm_provider_error(self):
        from core.services.llm.exceptions import LLMProviderError

        service = _make_service()
        secondary = AsyncMock()
        secondary._generate_with_retry = AsyncMock(
            side_effect=RuntimeError("also down")
        )
        with (
            patch.object(
                service,
                "_generate_with_retry",
                AsyncMock(side_effect=RuntimeError("down")),
            ),
            patch(
                "core.services.llm.fallback_runtime._clone_service",
                return_value=secondary,
            ),
        ):
            with pytest.raises(LLMProviderError, match="All providers failed"):
                await run_with_fallback(service, "p", model="llama3.2", json_mode=False)


class TestStructuredFallback:
    async def test_no_chain_calls_primary_directly(self):
        from core.services.llm.fallback_runtime import (
            maybe_run_structured_with_fallback,
        )

        service = _make_service(fallback_chain="")
        with patch(
            "core.services.llm.structured._native_with_retry",
            AsyncMock(return_value="RESULT"),
        ) as native:
            result, provider_name = await maybe_run_structured_with_fallback(
                service, "p", "llama3.2"
            )
        assert (result, provider_name) == ("RESULT", "ollama")
        native.assert_awaited_once()

    async def test_falls_through_to_native_capable_stage(self):
        from core.services.llm.fallback_runtime import (
            maybe_run_structured_with_fallback,
        )

        service = _make_service()
        clone = _make_service(fallback_chain="")
        clone.provider.supports_native_tools = True

        async def _native(svc, prompt, model, **kwargs):
            if svc is service:
                raise RuntimeError("primary down")
            return "SAVED"

        with (
            patch(
                "core.services.llm.structured._native_with_retry",
                AsyncMock(side_effect=_native),
            ),
            patch(
                "core.services.llm.fallback_runtime._clone_service",
                return_value=clone,
            ),
        ):
            result, provider_name = await maybe_run_structured_with_fallback(
                service, "p", "llama3.2"
            )
        assert (result, provider_name) == ("SAVED", "openai")

    async def test_stage_without_native_support_is_skipped(self):
        from core.services.llm.exceptions import LLMProviderError
        from core.services.llm.fallback_runtime import (
            maybe_run_structured_with_fallback,
        )

        service = _make_service()
        clone = _make_service(fallback_chain="")
        clone.provider.supports_native_tools = False

        with (
            patch(
                "core.services.llm.structured._native_with_retry",
                AsyncMock(side_effect=RuntimeError("primary down")),
            ),
            patch(
                "core.services.llm.fallback_runtime._clone_service",
                return_value=clone,
            ),
        ):
            with pytest.raises(LLMProviderError):
                await maybe_run_structured_with_fallback(service, "p", "llama3.2")

    async def test_budget_error_is_fatal_no_fallthrough_structured(self):
        from core.orchestration.limits import BudgetExceededError, LoopBudget
        from core.services.llm.fallback_runtime import (
            maybe_run_structured_with_fallback,
        )

        service = _make_service()
        budget = LoopBudget()
        with patch(
            "core.services.llm.structured._native_with_retry",
            AsyncMock(side_effect=BudgetExceededError("max_tokens", budget.snapshot())),
        ):
            with pytest.raises(BudgetExceededError):
                await maybe_run_structured_with_fallback(service, "p", "llama3.2")
