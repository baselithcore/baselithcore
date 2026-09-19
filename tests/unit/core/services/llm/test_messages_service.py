"""Service-level message API: routing, degradation, accounting.

``LLMService.generate_messages`` is the seam between an agent loop that keeps
a real conversation and a provider fleet where only some members can receive
one. A provider without the message API must still get the conversation — as a
rendered transcript — rather than the loop silently losing its history.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.services.llm.messages import Message, ToolResultBlock, ToolUseBlock
from core.services.llm.service import LLMService
from core.services.llm.tool_calling import LLMResult, LLMToolSpec

HISTORY = [
    Message.user("population of Rome?"),
    Message(
        role="assistant",
        content=[ToolUseBlock(id="t1", name="pop", input={"city": "Rome"})],
    ),
    Message.tool_results([ToolResultBlock(tool_use_id="t1", content="2870000")]),
]


def _service(enable_native_tools=True):
    with patch("core.services.llm.service.get_llm_config") as mock_config:
        # A stub provider is swapped in below; "ollama" only keeps the factory
        # from building a real client during construction.
        mock_config.return_value = Mock(
            provider="ollama",
            model="claude-opus-5",
            enable_cache=False,
            enable_native_tools=enable_native_tools,
            fallback_chain="",
            max_concurrent_requests=0,
        )
        return LLMService()


@pytest.mark.asyncio
class TestRouting:
    async def test_a_message_capable_provider_receives_the_history(self):
        service = _service()
        service.provider = Mock(supports_native_tools=True, supports_messages=True)
        service.provider.generate_messages = AsyncMock(
            return_value=LLMResult(text="2.87 million")
        )

        result = await service.generate_messages(
            HISTORY, system="be terse", tools=[LLMToolSpec(name="pop", description="d")]
        )

        assert result.text == "2.87 million"
        kwargs = service.provider.generate_messages.await_args.kwargs
        assert service.provider.generate_messages.await_args.args[0] is HISTORY
        assert kwargs["system"] == "be terse"
        assert kwargs["tools"][0].name == "pop"

    async def test_model_override_beats_the_configured_default(self):
        service = _service()
        service.provider = Mock(supports_native_tools=True, supports_messages=True)
        service.provider.generate_messages = AsyncMock(return_value=LLMResult(text="k"))

        await service.generate_messages(HISTORY, model="claude-sonnet-5")
        assert (
            service.provider.generate_messages.await_args.args[1] == "claude-sonnet-5"
        )


@pytest.mark.asyncio
class TestDegradation:
    async def test_a_provider_without_the_message_api_gets_a_transcript(self):
        """The history still reaches it — flattened, never dropped."""
        service = _service()
        service.provider = Mock(supports_native_tools=True, supports_messages=False)
        service.provider.generate_structured = AsyncMock(
            return_value=LLMResult(text="2.87 million")
        )

        result = await service.generate_messages(HISTORY, system="be terse")

        assert result.text == "2.87 million"
        prompt = service.provider.generate_structured.await_args.args[0]
        assert "population of Rome?" in prompt
        assert "2870000" in prompt

    async def test_the_transcript_carries_a_convergence_nudge(self):
        """Flattened text has no tool_result block saying the work came back."""
        service = _service()
        service.provider = Mock(supports_native_tools=True, supports_messages=False)
        service.provider.generate_structured = AsyncMock(
            return_value=LLMResult(text="done")
        )

        await service.generate_messages(HISTORY)
        prompt = service.provider.generate_structured.await_args.args[0]
        assert "answer without calling more tools" in prompt

    async def test_a_history_without_tool_results_gets_no_nudge(self):
        service = _service()
        service.provider = Mock(supports_native_tools=True, supports_messages=False)
        service.provider.generate_structured = AsyncMock(
            return_value=LLMResult(text="done")
        )

        await service.generate_messages([Message.user("hi")])
        prompt = service.provider.generate_structured.await_args.args[0]
        assert "answer without calling more tools" not in prompt

    async def test_the_degraded_result_still_carries_an_assistant_turn(self):
        service = _service()
        service.provider = Mock(supports_native_tools=True, supports_messages=False)
        service.provider.generate_structured = AsyncMock(
            return_value=LLMResult(text="done")
        )

        result = await service.generate_messages(HISTORY)
        from core.services.llm.messages import message_from_result

        assert message_from_result(result).text == "done"

    async def test_native_tools_disabled_also_degrades(self):
        service = _service(enable_native_tools=False)
        service.provider = Mock(supports_native_tools=True, supports_messages=True)
        service.provider.generate_messages = AsyncMock(return_value=LLMResult(text="x"))
        service.provider.generate = AsyncMock(return_value=("x", 3))

        await service.generate_messages(HISTORY)
        service.provider.generate_messages.assert_not_awaited()


@pytest.mark.asyncio
class TestCrossProviderFailover:
    """A configured chain must survive the agent taking the message path.

    The structured path it replaced routed through the fallback chain; calling
    the provider directly would drop failover silently, and a deployment would
    discover it the moment its primary went down.
    """

    def _chained(self, chain):
        service = _service()
        service.config.fallback_chain = chain
        service.provider = Mock(supports_native_tools=True, supports_messages=True)
        return service

    async def test_a_failing_primary_is_served_by_the_next_stage(self, monkeypatch):
        from core.services.llm import fallback_runtime
        from core.services.llm.exceptions import LLMProviderError

        fallback_runtime.reset_fallback_services()
        service = self._chained("anthropic:claude-sonnet-5")
        service.provider.generate_messages = AsyncMock(
            side_effect=LLMProviderError("primary down")
        )

        stage = Mock(supports_native_tools=True, supports_messages=True)
        stage.generate_messages = AsyncMock(return_value=LLMResult(text="from stage 2"))
        clone = Mock(provider=stage, config=service.config)
        monkeypatch.setattr(fallback_runtime, "_clone_service", lambda *a, **k: clone)

        result = await service.generate_messages(HISTORY)
        assert result.text == "from stage 2"
        stage.generate_messages.assert_awaited_once()

    async def test_a_stage_without_a_message_api_is_skipped(self, monkeypatch):
        from core.services.llm import fallback_runtime
        from core.services.llm.exceptions import LLMProviderError

        fallback_runtime.reset_fallback_services()
        service = self._chained("ollama:llama3.2")
        service.provider.generate_messages = AsyncMock(
            side_effect=LLMProviderError("primary down")
        )

        stage = Mock(supports_native_tools=True, supports_messages=False)
        stage.generate_messages = AsyncMock(return_value=LLMResult(text="wrong shape"))
        clone = Mock(provider=stage, config=service.config)
        monkeypatch.setattr(fallback_runtime, "_clone_service", lambda *a, **k: clone)

        with pytest.raises(LLMProviderError):
            await service.generate_messages(HISTORY)
        # Never called: a transcript-only stage would answer a different
        # conversation than the one the caller sent.
        stage.generate_messages.assert_not_awaited()

    async def test_no_chain_means_a_direct_provider_call(self):
        service = self._chained("")
        service.provider.generate_messages = AsyncMock(return_value=LLMResult(text="x"))
        assert (await service.generate_messages(HISTORY)).text == "x"


@pytest.mark.asyncio
class TestAccounting:
    async def test_the_turn_is_charged_against_the_ambient_budget_once(self):
        from core.orchestration.budget_context import activate_budget, deactivate_budget
        from core.orchestration.limits import LoopBudget, LoopLimits

        service = _service()
        # A PAID provider on purpose: a turn served locally is priced at zero
        # by design (self-hosted inference is capacity-bound, not price-bound),
        # so a dollar assertion needs a provider that actually bills. The stub
        # provider below answers either way.
        service.config.provider = "anthropic"
        service.provider = Mock(supports_native_tools=True, supports_messages=True)
        service.provider.generate_messages = AsyncMock(
            return_value=LLMResult(text="ok", tokens_used=100)
        )

        budget = LoopBudget(limits=LoopLimits(budget_usd=10.0))
        token = activate_budget(budget)
        try:
            await service.generate_messages(HISTORY)
        finally:
            deactivate_budget(token)

        assert budget.cost_usd > 0
