"""An LLM outage must end a ReAct run as an error, never as an answer.

``_get_llm_service`` swallowed the resolution failure without a log line and
``_call_llm`` returned ``"Final Answer: LLM service unavailable."``, which the
loop parsed as the model's answer — the handler then returned it as a
successful response.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from core.reasoning.react import (
    LLM_ERROR_ANSWER,
    LLM_UNAVAILABLE_ANSWER,
    ReActAgent,
)


async def test_unresolvable_llm_is_logged_and_flagged(monkeypatch):
    def _boom():
        raise RuntimeError("no provider configured")

    logger = Mock()
    monkeypatch.setattr("core.services.llm.get_llm_service", _boom)
    monkeypatch.setattr("core.reasoning.react.logger", logger)
    agent = ReActAgent(native_tools=False)

    result = await agent.run("q")

    assert result.error == "llm_unavailable"
    assert result.final_answer == LLM_UNAVAILABLE_ANSWER
    logged = [str(c.args[1]) for c in logger.error.call_args_list if len(c.args) > 1]
    assert "no provider configured" in logged


async def test_failing_llm_call_is_flagged():
    llm = AsyncMock()
    llm.generate_response = AsyncMock(side_effect=RuntimeError("503"))
    agent = ReActAgent(llm_service=llm, native_tools=False)

    result = await agent.run("q")

    assert result.error == "llm_error"
    assert result.final_answer == LLM_ERROR_ANSWER
    assert result.iterations_used == 1


async def test_successful_run_has_no_error():
    llm = AsyncMock()
    llm.generate_response = AsyncMock(return_value="Final Answer: 42")
    agent = ReActAgent(llm_service=llm, native_tools=False)

    result = await agent.run("q")

    assert result.error is None
    assert result.final_answer == "42"
