"""Provider-level message API: wire shape, cache breakpoints, prefix stability.

The point of the message loop is not tidiness — it is that the prompt prefix
stops changing between turns, so the system prompt, the tool schemas and every
completed turn can be served from the prompt cache instead of re-billed. These
tests assert the *request shape* that makes that true.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.services.llm.messages import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from core.services.llm.tool_calling import LLMToolSpec

MODERN = "claude-opus-5"
# Long enough to clear the provider's cacheable-prefix floor (4096 chars);
# a shorter one is silently ignored by the API, so a breakpoint on it proves
# nothing.
LONG_SYSTEM = "You are a careful agent. " * 200

WEATHER = LLMToolSpec(
    name="weather",
    description="Look up the weather. " * 200,
    parameters={"type": "object", "properties": {"city": {"type": "string"}}},
)


def _response(blocks=None, stop_reason="end_turn"):
    response = MagicMock()
    response.content = blocks if blocks is not None else []
    response.stop_reason = stop_reason
    response.stop_details = None
    response.usage.input_tokens = 10
    response.usage.output_tokens = 5
    response.usage.cache_creation_input_tokens = 0
    response.usage.cache_read_input_tokens = 0
    return response


def _block(**fields):
    block = MagicMock()
    for key, value in fields.items():
        setattr(block, key, value)
    return block


def _anthropic(client):
    with patch(
        "core.services.llm.providers.anthropic_provider.anthropic"
    ) as mock_anthropic:
        mock_anthropic.AsyncAnthropic.return_value = client
        mock_anthropic.NOT_GIVEN = "not_given"
        from core.services.llm.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(api_key="sk-ant-test")
        provider._ensure_client()
        return provider


def _without_breakpoints(wire):
    """The wire messages with every ``cache_control`` marker removed."""
    return [
        {
            **message,
            "content": [
                {k: v for k, v in block.items() if k != "cache_control"}
                for block in message["content"]
            ],
        }
        for message in wire
    ]


def _client(response=None):
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response or _response())
    return client


@pytest.mark.asyncio
class TestAnthropicMessages:
    async def test_provider_advertises_the_message_api(self):
        assert _anthropic(_client()).supports_messages is True

    async def test_history_is_sent_as_messages_not_a_rebuilt_prompt(self):
        client = _client()
        provider = _anthropic(client)
        await provider.generate_messages(
            [
                Message.user("population of Rome?"),
                Message(
                    role="assistant",
                    content=[ToolUseBlock(id="t1", name="pop", input={"city": "Rome"})],
                ),
                Message.tool_results(
                    [ToolResultBlock(tool_use_id="t1", content="2870000")]
                ),
            ],
            MODERN,
        )
        sent = client.messages.create.call_args.kwargs["messages"]
        assert [m["role"] for m in sent] == ["user", "assistant", "user"]
        assert sent[1]["content"][0]["type"] == "tool_use"
        assert sent[2]["content"][0]["tool_use_id"] == "t1"

    async def test_failed_tool_results_carry_is_error(self):
        client = _client()
        provider = _anthropic(client)
        await provider.generate_messages(
            [
                Message.tool_results(
                    [ToolResultBlock(tool_use_id="t1", content="boom", is_error=True)]
                )
            ],
            MODERN,
        )
        sent = client.messages.create.call_args.kwargs["messages"]
        assert sent[0]["content"][0]["is_error"] is True

    async def test_system_prompt_carries_the_cache_breakpoint(self):
        client = _client()
        provider = _anthropic(client)
        await provider.generate_messages(
            [Message.user("hi")], MODERN, system=LONG_SYSTEM
        )
        system = client.messages.create.call_args.kwargs["system"]
        assert system[0]["text"] == LONG_SYSTEM
        assert system[0]["cache_control"] == {"type": "ephemeral"}

    async def test_tool_schemas_carry_their_own_breakpoint(self):
        client = _client()
        provider = _anthropic(client)
        await provider.generate_messages([Message.user("hi")], MODERN, tools=[WEATHER])
        tools = client.messages.create.call_args.kwargs["tools"]
        assert tools[-1]["cache_control"] == {"type": "ephemeral"}

    async def test_completed_turns_are_a_stable_prefix_across_iterations(self):
        """The whole point: turn two re-sends turn one byte-for-byte.

        The old loop rebuilt the user prompt each iteration, so every turn
        invalidated the prefix behind it and nothing could be cached. Compared
        through ``json.dumps`` of the rendered wire, so an in-place edit of a
        nested content block cannot hide behind a shallow-copy comparison.
        """
        client = _client()
        provider = _anthropic(client)
        history = [Message.user("population of Rome?")]
        await provider.generate_messages(list(history), MODERN, system=LONG_SYSTEM)
        first = client.messages.create.call_args.kwargs

        history.append(
            Message(
                role="assistant",
                content=[ToolUseBlock(id="t1", name="pop", input={"city": "Rome"})],
            )
        )
        history.append(
            Message.tool_results([ToolResultBlock(tool_use_id="t1", content="2870000")])
        )
        await provider.generate_messages(list(history), MODERN, system=LONG_SYSTEM)
        second = client.messages.create.call_args.kwargs

        assert json.dumps(second["system"]) == json.dumps(first["system"])
        assert len(second["messages"]) == 3
        # The first turn's *content* is re-sent unchanged. Only the breakpoint
        # moves (it is request metadata, not part of the cached content), so it
        # is stripped before the comparison.
        assert json.dumps(_without_breakpoints(second["messages"])[:1]) == json.dumps(
            _without_breakpoints(first["messages"])
        )

    async def test_the_growing_history_gets_a_rotating_breakpoint(self):
        """A breakpoint on the last block caches everything before it."""
        client = _client()
        provider = _anthropic(client)
        long_result = "x" * 5000
        await provider.generate_messages(
            [
                Message.user("q"),
                Message(
                    role="assistant",
                    content=[ToolUseBlock(id="t1", name="pop", input={})],
                ),
                Message.tool_results(
                    [ToolResultBlock(tool_use_id="t1", content=long_result)]
                ),
            ],
            MODERN,
        )
        sent = client.messages.create.call_args.kwargs["messages"]
        assert sent[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in sent[0]["content"][-1]

    async def test_the_floor_is_measured_against_the_whole_prefix(self):
        """System + tools + messages, not each segment on its own.

        Anthropic's cacheable minimum applies to the cumulative prefix. Judging
        the segments separately means this perfectly cacheable request — ~1715
        tokens, well over the ~1024-token floor — emits no breakpoint at all
        and re-bills the entire prefix on every turn of the loop.
        """
        client = _client()
        provider = _anthropic(client)
        system = "s" * 251
        tools = [
            LLMToolSpec(
                name="search",
                description="d" * 3400,
                parameters={"type": "object", "properties": {"q": {"type": "string"}}},
            )
        ]
        history = [
            Message.user("q"),
            Message(
                role="assistant",
                content=[ToolUseBlock(id="t1", name="search", input={"q": "x"})],
            ),
            Message.tool_results(
                [ToolResultBlock(tool_use_id="t1", content="r" * 2900)]
            ),
        ]
        await provider.generate_messages(history, MODERN, system=system, tools=tools)
        kwargs = client.messages.create.call_args.kwargs

        # Each segment alone is under the 4096-char floor...
        assert len(str(kwargs["system"])) < 4096
        assert sum(len(str(e)) for e in kwargs["messages"]) < 4096
        # ...but the prefix they form is not, so the breakpoint is spent.
        assert kwargs["messages"][-1]["content"][-1]["cache_control"] == {
            "type": "ephemeral"
        }

    async def test_short_histories_do_not_spend_a_breakpoint(self):
        client = _client()
        provider = _anthropic(client)
        await provider.generate_messages([Message.user("hi")], MODERN)
        sent = client.messages.create.call_args.kwargs["messages"]
        assert "cache_control" not in sent[0]["content"][-1]

    async def test_the_assistant_turn_comes_back_with_thinking_intact(self):
        response = _response(
            [
                _block(type="thinking", thinking="reasoning", signature="sig-1"),
                _block(type="tool_use", id="t1", name="pop", input={"city": "Rome"}),
            ],
            stop_reason="tool_use",
        )
        provider = _anthropic(_client(response))
        result = await provider.generate_messages([Message.user("q")], MODERN)

        assert result.tool_calls[0].id == "t1"
        assert result.message is not None
        assert isinstance(result.message.content[0], ThinkingBlock)
        assert result.message.content[0].payload["signature"] == "sig-1"

    async def test_usage_is_metered_from_the_response(self):
        provider = _anthropic(_client(_response([_block(type="text", text="ok")])))
        result = await provider.generate_messages([Message.user("q")], MODERN)
        assert result.text == "ok"
        assert result.usage.input_tokens == 10
        assert result.usage.output_tokens == 5


def _openai(client):
    with patch("core.services.llm.providers.openai_provider.openai") as mock_openai:
        mock_openai.AsyncOpenAI.return_value = client
        from core.services.llm.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key="sk-test")
        provider._ensure_client()
        return provider


def _openai_response(content="ok", tool_calls=None):
    response = MagicMock()
    message = MagicMock()
    message.content = content
    message.refusal = None
    message.tool_calls = tool_calls or []
    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "tool_calls" if tool_calls else "stop"
    response.choices = [choice]
    response.usage.prompt_tokens = 12
    response.usage.completion_tokens = 4
    response.usage.total_tokens = 16
    response.usage.prompt_tokens_details = None
    return response


def _openai_client(response=None):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=response or _openai_response()
    )
    return client


@pytest.mark.asyncio
class TestOpenAIMessages:
    async def test_provider_advertises_the_message_api(self):
        assert _openai(_openai_client()).supports_messages is True

    async def test_tool_results_become_tool_messages(self):
        client = _openai_client()
        provider = _openai(client)
        await provider.generate_messages(
            [
                Message.user("q"),
                Message(
                    role="assistant",
                    content=[ToolUseBlock(id="t1", name="pop", input={"city": "Rome"})],
                ),
                Message.tool_results(
                    [ToolResultBlock(tool_use_id="t1", content="2870000")]
                ),
            ],
            "gpt-5",
            system="be terse",
        )
        sent = client.chat.completions.create.call_args.kwargs["messages"]
        assert sent[0] == {"role": "system", "content": "be terse"}
        assert sent[2]["tool_calls"][0]["function"]["name"] == "pop"
        assert sent[3] == {
            "role": "tool",
            "tool_call_id": "t1",
            "content": "2870000",
        }

    async def test_tool_calls_are_parsed_back_into_the_assistant_turn(self):
        call = MagicMock()
        call.id = "t9"
        call.function.name = "pop"
        call.function.arguments = json.dumps({"city": "Rome"})
        provider = _openai(_openai_client(_openai_response(None, [call])))
        result = await provider.generate_messages([Message.user("q")], "gpt-5")

        assert result.tool_calls[0].arguments == {"city": "Rome"}
        assert result.message is not None
        assert result.message.tool_uses[0].id == "t9"

    async def test_output_cap_uses_the_current_parameter_name(self):
        client = _openai_client()
        provider = _openai(client)
        await provider.generate_messages(
            [Message.user("q")], "gpt-5", max_tokens=256, tools=[WEATHER]
        )
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["max_completion_tokens"] == 256
        assert kwargs["tools"][0]["function"]["name"] == "weather"


class TestProvidersWithoutAMessageAPI:
    """Every other provider stays on the string path until it implements one."""

    @pytest.mark.parametrize(
        "module, name",
        [
            ("ollama_provider", "OllamaProvider"),
            ("gemini_provider", "GeminiProvider"),
            ("huggingface_provider", "HuggingFaceProvider"),
        ],
    )
    def test_supports_messages_is_false(self, module, name):
        import importlib

        provider_cls = getattr(
            importlib.import_module(f"core.services.llm.providers.{module}"), name
        )
        assert getattr(provider_cls, "supports_messages", False) is False


@pytest.mark.asyncio
class TestNeutralBlocksSurviveTheRoundTrip:
    async def test_text_blocks_of_a_multi_block_turn_are_preserved(self):
        client = _client()
        provider = _anthropic(client)
        await provider.generate_messages(
            [
                Message(
                    role="assistant",
                    content=[
                        TextBlock(text="thinking out loud"),
                        ToolUseBlock(id="t1", name="pop", input={}),
                    ],
                )
            ],
            MODERN,
        )
        sent = client.messages.create.call_args.kwargs["messages"]
        assert sent[0]["content"][0] == {"type": "text", "text": "thinking out loud"}
