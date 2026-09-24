"""The vLLM provider hands back answers, not a thinking model's reasoning.

Shapes are what a parser-less vLLM server really sends: the reasoning inline,
closed by ``</think>`` with no opening tag.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.services.llm.messages import Message, TextBlock
from core.services.llm.tool_calling import LLMResult

_SDK = "core.services.llm.providers.openai_provider.openai"
_LEAKY = "Here's a thinking process:\n1. x\n</think>\n\nciao"


def _completion(text: str) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = text
    response.choices[0].message.refusal = None
    response.choices[0].finish_reason = "stop"
    response.usage.total_tokens = 9
    return response


def _chunk(content=None, reasoning=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)


async def _aiter(items):
    for item in items:
        yield item


@pytest.fixture
def provider():
    from core.services.llm.providers.vllm_provider import VLLMProvider

    return VLLMProvider(api_base="http://gpu:8002")


async def test_generate_drops_inline_reasoning(provider):
    client = AsyncMock()
    client.chat.completions.create.return_value = _completion(_LEAKY)
    with patch(_SDK) as sdk:
        sdk.AsyncOpenAI.return_value = client
        text, _ = await provider.generate("hi", model="qwen")
    assert text == "ciao"


async def test_structured_and_message_results_are_cleaned(provider):
    result = LLMResult(
        text=_LEAKY,
        message=Message(role="assistant", content=[TextBlock(text=_LEAKY)]),
    )
    with patch(
        "core.services.llm.providers.vllm_provider._generate_messages",
        AsyncMock(return_value=result),
    ):
        out = await provider.generate_messages([], model="qwen")
    assert out.text == "ciao"
    assert out.message.content[0].text == "ciao"


async def test_stream_holds_back_reasoning(provider):
    chunks = [
        _chunk("Here's a thinking"),
        _chunk(" process\n</thi"),
        _chunk("nk>\n\nci"),
        _chunk("ao"),
    ]
    client = AsyncMock()
    client.chat.completions.create.return_value = _aiter(chunks)
    with patch(_SDK) as sdk:
        sdk.AsyncOpenAI.return_value = client
        texts = [t async for t, _ in provider.generate_stream("hi", model="qwen")]
    assert "".join(texts) == "ciao"
    assert not any("thinking" in t for t in texts)


async def test_a_parsing_server_streams_without_holding_back(provider):
    """With --reasoning-parser the reasoning arrives in its own field."""
    chunks = [_chunk(reasoning="thinking…"), _chunk("ci"), _chunk("ao")]
    client = AsyncMock()
    client.chat.completions.create.return_value = _aiter(chunks)
    with patch(_SDK) as sdk:
        sdk.AsyncOpenAI.return_value = client
        texts = [t async for t, _ in provider.generate_stream("hi", model="qwen")]
    assert [t for t in texts if t] == ["ci", "ao"]
