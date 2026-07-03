"""Contract tests for native tool-calling / structured outputs.

Covers the neutral types, each provider's native wire-shape mapping + parse-back,
and the service-level native-vs-fallback routing (including the prompt-coercion
fallback and the unchanged legacy string path).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config.services import LLMConfig
from core.services.llm import (
    ANY,
    AUTO,
    NONE,
    LLMResult,
    LLMService,
    LLMToolSpec,
    ResponseFormat,
    ToolCall,
    ToolChoice,
    tool_spec_from_mcp,
)

pytestmark = [pytest.mark.contract]

WEATHER = LLMToolSpec(
    name="get_weather",
    description="Get weather for a city",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)


# --------------------------------------------------------------------------- #
# Neutral types
# --------------------------------------------------------------------------- #


class TestNeutralTypes:
    def test_tool_choice_forced_requires_name(self):
        with pytest.raises(ValueError):
            ToolChoice(mode="tool")

    def test_tool_choice_name_only_with_tool_mode(self):
        with pytest.raises(ValueError):
            ToolChoice(mode="auto", name="x")

    def test_tool_choice_forced_factory(self):
        tc = ToolChoice.forced("get_weather")
        assert tc.mode == "tool"
        assert tc.name == "get_weather"

    def test_singletons(self):
        assert AUTO.mode == "auto"
        assert ANY.mode == "any"
        assert NONE.mode == "none"

    def test_llmresult_has_tool_calls(self):
        assert not LLMResult(text="hi").has_tool_calls
        assert LLMResult(tool_calls=[ToolCall(id="1", name="t")]).has_tool_calls

    def test_tool_spec_from_mcp(self):
        from core.mcp.types import MCPTool

        mcp = MCPTool(
            name="search",
            description="search the web",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        )
        spec = tool_spec_from_mcp(mcp)
        assert spec.name == "search"
        assert spec.parameters == mcp.input_schema

    def test_tool_spec_from_mcp_empty_schema_defaults(self):
        from core.mcp.types import MCPTool

        spec = tool_spec_from_mcp(MCPTool(name="x", description="d", input_schema={}))
        assert spec.parameters == {"type": "object"}


# --------------------------------------------------------------------------- #
# Anthropic native mapping
# --------------------------------------------------------------------------- #


def _anthropic_block(btype, **attrs):
    b = MagicMock()
    b.type = btype
    for k, v in attrs.items():
        setattr(b, k, v)
    return b


def _anthropic_response(blocks, in_tok=10, out_tok=5, stop_reason="end_turn"):
    resp = MagicMock()
    resp.content = blocks
    resp.stop_reason = stop_reason
    resp.usage.input_tokens = in_tok
    resp.usage.output_tokens = out_tok
    resp.usage.cache_creation_input_tokens = 0
    resp.usage.cache_read_input_tokens = 0
    return resp


@pytest.mark.asyncio
class TestAnthropicNative:
    async def _provider(self, response):
        from core.services.llm.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(api_key="sk-ant-test")
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=response)
        provider.client = mock_client  # bypass lazy _ensure_client
        return provider, mock_client

    async def test_parses_tool_use_block(self):
        resp = _anthropic_response(
            [
                _anthropic_block("text", text="Let me check."),
                _anthropic_block(
                    "tool_use", id="toolu_1", name="get_weather", input={"city": "NYC"}
                ),
            ],
            stop_reason="tool_use",
        )
        provider, client = await self._provider(resp)
        result = await provider.generate_structured(
            "weather?", "claude-3-sonnet", tools=[WEATHER]
        )
        assert result.native is True
        assert result.stop_reason == "tool_use"
        assert result.text == "Let me check."
        assert result.tool_calls == [
            ToolCall(id="toolu_1", name="get_weather", arguments={"city": "NYC"})
        ]

    async def test_tools_and_tool_choice_wire_shape(self):
        provider, client = await self._provider(
            _anthropic_response([_anthropic_block("text", text="hi")])
        )
        await provider.generate_structured(
            "x",
            "claude-3-sonnet",
            tools=[WEATHER],
            tool_choice=ToolChoice.forced("get_weather"),
        )
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["tools"] == [
            {
                "name": "get_weather",
                "description": "Get weather for a city",
                "input_schema": WEATHER.parameters,
            }
        ]
        assert kwargs["tool_choice"] == {"type": "tool", "name": "get_weather"}

    async def test_tool_choice_any_and_none_map_directly(self):
        provider, client = await self._provider(
            _anthropic_response([_anthropic_block("text", text="hi")])
        )
        await provider.generate_structured("x", "m", tools=[WEATHER], tool_choice=ANY)
        assert client.messages.create.call_args.kwargs["tool_choice"] == {"type": "any"}

    async def test_response_format_maps_to_output_config(self):
        provider, client = await self._provider(
            _anthropic_response([_anthropic_block("text", text="{}")])
        )
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
        await provider.generate_structured(
            "x", "m", response_format=ResponseFormat(schema=schema)
        )
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["output_config"] == {
            "format": {"type": "json_schema", "schema": schema}
        }

    async def test_strict_tool_adds_flag(self):
        provider, client = await self._provider(
            _anthropic_response([_anthropic_block("text", text="hi")])
        )
        strict = LLMToolSpec(name="t", description="d", strict=True)
        await provider.generate_structured("x", "m", tools=[strict])
        assert client.messages.create.call_args.kwargs["tools"][0]["strict"] is True


# --------------------------------------------------------------------------- #
# OpenAI native mapping
# --------------------------------------------------------------------------- #


def _openai_response(content=None, tool_calls=None, finish="stop", total=20):
    resp = MagicMock()
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = finish
    resp.choices = [choice]
    resp.usage.total_tokens = total
    return resp


def _openai_tool_call(cid, name, args_json):
    call = MagicMock()
    call.id = cid
    call.function.name = name
    call.function.arguments = args_json
    return call


@pytest.mark.asyncio
class TestOpenAINative:
    async def _provider(self, response):
        from core.services.llm.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key="sk-test")
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=response)
        provider.client = mock_client  # bypass lazy _ensure_client
        return provider, mock_client

    async def test_parses_tool_calls_and_json_args(self):
        resp = _openai_response(
            tool_calls=[_openai_tool_call("call_1", "get_weather", '{"city": "LA"}')],
            finish="tool_calls",
        )
        provider, client = await self._provider(resp)
        result = await provider.generate_structured("x", "gpt-4o", tools=[WEATHER])
        assert result.stop_reason == "tool_calls"
        assert result.tool_calls == [
            ToolCall(id="call_1", name="get_weather", arguments={"city": "LA"})
        ]

    async def test_malformed_args_preserved_not_dropped(self):
        resp = _openai_response(
            tool_calls=[_openai_tool_call("call_1", "t", "not json{")]
        )
        provider, _ = await self._provider(resp)
        result = await provider.generate_structured("x", "gpt-4o", tools=[WEATHER])
        assert result.tool_calls[0].arguments == {"_raw": "not json{"}

    async def test_tools_wire_shape_and_tool_choice(self):
        provider, client = await self._provider(_openai_response(content="hi"))
        await provider.generate_structured(
            "x", "gpt-4o", tools=[WEATHER], tool_choice=ANY
        )
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["tools"][0]["type"] == "function"
        assert kwargs["tools"][0]["function"]["name"] == "get_weather"
        assert kwargs["tool_choice"] == "required"

    async def test_forced_tool_choice_shape(self):
        provider, client = await self._provider(_openai_response(content="hi"))
        await provider.generate_structured(
            "x", "gpt-4o", tools=[WEATHER], tool_choice=ToolChoice.forced("get_weather")
        )
        assert client.chat.completions.create.call_args.kwargs["tool_choice"] == {
            "type": "function",
            "function": {"name": "get_weather"},
        }

    async def test_response_format_json_schema(self):
        provider, client = await self._provider(_openai_response(content="{}"))
        schema = {"type": "object"}
        await provider.generate_structured(
            "x", "gpt-4o", response_format=ResponseFormat(schema=schema, name="out")
        )
        rf = client.chat.completions.create.call_args.kwargs["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["name"] == "out"
        assert rf["json_schema"]["schema"] == schema


# --------------------------------------------------------------------------- #
# Ollama native mapping
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestOllamaNative:
    async def _provider(self, response):
        from core.services.llm.providers.ollama_provider import OllamaProvider

        provider = OllamaProvider()
        mock_client = MagicMock()
        mock_client.chat = AsyncMock(return_value=response)
        provider.client = mock_client
        return provider, mock_client

    async def test_parses_dict_shaped_tool_calls_with_synth_ids(self):
        response = {
            "message": {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": "NYC"}}}
                ],
            },
            "eval_count": 5,
            "prompt_eval_count": 3,
        }
        provider, _ = await self._provider(response)
        result = await provider.generate_structured("x", "llama3", tools=[WEATHER])
        assert result.tool_calls == [
            ToolCall(id="ollama-call-0", name="get_weather", arguments={"city": "NYC"})
        ]
        assert result.tokens_used == 8

    async def test_tool_choice_none_omits_tools(self):
        provider, client = await self._provider({"message": {"content": "hi"}})
        await provider.generate_structured(
            "x", "llama3", tools=[WEATHER], tool_choice=NONE
        )
        assert "tools" not in client.chat.call_args.kwargs

    async def test_response_format_passed_as_format(self):
        provider, client = await self._provider({"message": {"content": "{}"}})
        schema = {"type": "object"}
        await provider.generate_structured(
            "x", "llama3", response_format=ResponseFormat(schema=schema)
        )
        assert client.chat.call_args.kwargs["format"] == schema


# --------------------------------------------------------------------------- #
# Service-level routing: native vs fallback
# --------------------------------------------------------------------------- #


def _service(enable_native_tools: bool) -> LLMService:
    config = LLMConfig(
        provider="ollama",
        model="llama3",
        enable_cache=False,
        enable_native_tools=enable_native_tools,
    )
    return LLMService(config=config, enable_cache=False)


@pytest.mark.asyncio
class TestServiceRouting:
    async def test_native_path_delegates_to_provider(self):
        service = _service(enable_native_tools=True)
        expected = LLMResult(
            text=None,
            tool_calls=[ToolCall(id="c1", name="get_weather", arguments={"city": "X"})],
            tokens_used=42,
            native=True,
        )
        service.provider = MagicMock()
        service.provider.supports_native_tools = True
        service.provider.generate_structured = AsyncMock(return_value=expected)

        result = await service.generate("weather?", tools=[WEATHER])
        assert result is expected
        service.provider.generate_structured.assert_awaited_once()

    async def test_unsupported_provider_uses_fallback_even_if_flag_on(self):
        service = _service(enable_native_tools=True)
        service.provider = MagicMock()
        service.provider.supports_native_tools = False  # e.g. HuggingFace
        service.provider.generate_structured = AsyncMock()
        service._generate_with_retry = AsyncMock(
            return_value=('{"tool": "get_weather", "arguments": {"city": "NYC"}}', 12)
        )

        result = await service.generate("weather?", tools=[WEATHER])
        service.provider.generate_structured.assert_not_awaited()
        assert result.native is False
        assert result.tool_calls == [
            ToolCall(
                id="fallback-call-0", name="get_weather", arguments={"city": "NYC"}
            )
        ]

    async def test_fallback_when_flag_off(self):
        service = _service(enable_native_tools=False)
        service.provider = MagicMock()
        service.provider.supports_native_tools = True  # capable, but flag off
        service.provider.generate_structured = AsyncMock()
        service._generate_with_retry = AsyncMock(
            return_value=('{"tool": null, "final": "It is sunny."}', 8)
        )

        result = await service.generate("weather?", tools=[WEATHER])
        service.provider.generate_structured.assert_not_awaited()
        assert result.text == "It is sunny."
        assert result.tool_calls == []

    async def test_fallback_augments_system_and_requests_json(self):
        service = _service(enable_native_tools=False)
        service._generate_with_retry = AsyncMock(
            return_value=('{"tool": "get_weather", "arguments": {}}', 5)
        )
        await service.generate("weather?", tools=[WEATHER], system_prompt="Be terse.")
        captured = service._generate_with_retry.call_args.kwargs
        assert captured["json_mode"] is True
        assert "Be terse." in captured["system"]
        assert "get_weather" in captured["system"]

    async def test_fallback_malformed_json_degrades_to_text(self):
        service = _service(enable_native_tools=False)
        service._generate_with_retry = AsyncMock(return_value=("plain answer", 4))
        result = await service.generate("hi", tools=[WEATHER])
        assert result.text == "plain answer"
        assert result.tool_calls == []

    async def test_response_format_only_fallback_returns_json_text(self):
        service = _service(enable_native_tools=False)
        service._generate_with_retry = AsyncMock(return_value=('{"n": 1}', 4))
        result = await service.generate(
            "x", response_format=ResponseFormat(schema={"type": "object"})
        )
        assert result.text == '{"n": 1}'
        assert result.tool_calls == []

    async def test_generate_response_still_returns_str(self):
        service = _service(enable_native_tools=False)
        service._generate_with_retry = AsyncMock(return_value=("hello", 3))
        out = await service.generate_response("hi")
        assert isinstance(out, str)
        assert out == "hello"


def test_config_flag_defaults_off():
    assert LLMConfig(provider="ollama").enable_native_tools is False
