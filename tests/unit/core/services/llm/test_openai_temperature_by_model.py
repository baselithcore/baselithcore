"""A reasoning model is never sent a temperature it rejects, on any OpenAI path.

``gpt-5`` and later (and the ``o`` series) answer 400 to any temperature but the
default 1. ``apply_reasoning_defaults`` already drops it, and the plain, the
streamed and the message paths call it; the structured path built its request by
hand and sent ``temperature=0.0`` as given, so every structured call that asked
for a deterministic answer (tenderx's document reader, hiredesk's talent search)
failed on the default model. Each path is driven through a fake SDK client, never
the network.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from baselith.core.services.llm.messages import Message

pytestmark = pytest.mark.asyncio

REASONING = "gpt-6-luna"
PLAIN = "gpt-4o-mini"


def _completion() -> SimpleNamespace:
    message = SimpleNamespace(
        content="ok", tool_calls=None, refusal=None, role="assistant"
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=2,
            total_tokens=12,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        ),
    )


def _provider() -> tuple[Any, AsyncMock]:
    create = AsyncMock(return_value=_completion())
    client = MagicMock()
    client.chat.completions.create = create
    with patch(
        "baselith.core.services.llm.providers.openai_provider.openai"
    ) as mock_openai:
        mock_openai.AsyncOpenAI.return_value = client
        from baselith.core.services.llm.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key="sk-test")
        provider._ensure_client()
    return provider, create


async def _generate(provider: Any, model: str, **kwargs: Any) -> None:
    await provider.generate("hi", model=model, **kwargs)


async def _structured(provider: Any, model: str, **kwargs: Any) -> None:
    await provider.generate_structured("hi", model=model, **kwargs)


async def _messages(provider: Any, model: str, **kwargs: Any) -> None:
    await provider.generate_messages([Message.user("hi")], model=model, **kwargs)


PATHS = pytest.mark.parametrize(
    "call", [_generate, _structured, _messages], ids=["plain", "structured", "messages"]
)


@PATHS
async def test_a_reasoning_model_is_not_sent_a_zero_temperature(call: Any) -> None:
    provider, create = _provider()

    await call(provider, REASONING, temperature=0.0)

    assert "temperature" not in create.call_args.kwargs


@PATHS
async def test_a_reasoning_model_keeps_the_default_temperature(call: Any) -> None:
    provider, create = _provider()

    await call(provider, REASONING, temperature=1)

    assert create.call_args.kwargs["temperature"] == 1


@PATHS
async def test_other_models_keep_the_temperature_the_caller_chose(call: Any) -> None:
    provider, create = _provider()

    await call(provider, PLAIN, temperature=0.0)

    assert create.call_args.kwargs["temperature"] == 0.0


@PATHS
async def test_no_temperature_is_invented_when_the_caller_sent_none(call: Any) -> None:
    provider, create = _provider()

    await call(provider, REASONING)

    assert "temperature" not in create.call_args.kwargs


async def test_the_structured_path_sends_no_reasoning_effort_of_its_own() -> None:
    """The fix is the temperature rule alone: the provider-level default effort
    stays what it was on this path."""
    provider, create = _provider()
    provider._reasoning_effort = "none"

    await _structured(provider, REASONING, temperature=0.0)

    assert "reasoning_effort" not in create.call_args.kwargs
