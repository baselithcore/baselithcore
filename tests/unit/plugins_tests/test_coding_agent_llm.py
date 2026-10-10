"""CodingAgent reads the text of what the LLM service returns, whatever its shape."""

from __future__ import annotations

from types import SimpleNamespace

from core.services.llm.tool_calling import LLMResult
from plugins.coding_agent.agent import CodingAgent


class FakeLLM:
    def __init__(self, response: object) -> None:
        self.response = response
        self.seen: dict[str, object] = {}

    async def generate(self, **kwargs: object) -> object:
        self.seen = kwargs
        return self.response


async def test_ask_llm_reads_the_text_of_an_llm_result():
    agent = CodingAgent()
    agent._llm = FakeLLM(LLMResult(text="print('hi')"))
    assert await agent._ask_llm("p") == "print('hi')"
    assert agent._llm.seen["prompt"] == "p" and agent._llm.seen["temperature"] == 0.0


async def test_ask_llm_still_reads_a_content_attribute():
    agent = CodingAgent()
    agent._llm = FakeLLM(SimpleNamespace(content="x = 1"))
    assert await agent._ask_llm("p") == "x = 1"


async def test_ask_llm_reads_an_empty_result_as_empty_text():
    agent = CodingAgent()
    agent._llm = FakeLLM(LLMResult(text=None))
    assert await agent._ask_llm("p") == ""
