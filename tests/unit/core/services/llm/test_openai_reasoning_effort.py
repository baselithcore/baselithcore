"""Provider-level default ``reasoning_effort`` for OpenAI requests."""

from baselith.core.config.services import LLMConfig
from baselith.core.services.llm.providers._openai_request import request_kwargs
from baselith.core.services.llm.providers.openai_provider import OpenAIProvider


def test_default_effort_is_sent_when_configured() -> None:
    assert request_kwargs({}, "none", "gpt-6-luna") == {"reasoning_effort": "none"}


def test_nothing_is_sent_without_a_default() -> None:
    assert "reasoning_effort" not in request_kwargs(
        {"temperature": 0.2}, None, "gpt-6-luna"
    )


def test_caller_effort_wins_over_default() -> None:
    out = request_kwargs({"reasoning_effort": "high"}, "none", "gpt-6-luna")
    assert out["reasoning_effort"] == "high"


def test_cross_provider_effort_hint_is_not_forwarded() -> None:
    assert request_kwargs({"effort": "high"}, "none", "gpt-6-luna") == {
        "reasoning_effort": "none"
    }


def test_default_skipped_for_non_reasoning_models() -> None:
    for model in (
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4.1",
        "gpt-3.5-turbo",
        "ft:gpt-4o-mini:x",
    ):
        assert "reasoning_effort" not in request_kwargs({}, "none", model)
    assert request_kwargs({}, "none", "gpt-6-luna") == {"reasoning_effort": "none"}


def test_explicit_caller_effort_still_forwarded_for_any_model() -> None:
    out = request_kwargs({"reasoning_effort": "low"}, "none", "gpt-4o-mini")
    assert out["reasoning_effort"] == "low"


def test_config_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_OPENAI_REASONING_EFFORT", "none")
    assert LLMConfig().openai_reasoning_effort == "none"


def test_config_blank_env_means_unset(monkeypatch) -> None:
    monkeypatch.setenv("LLM_OPENAI_REASONING_EFFORT", "")
    assert LLMConfig().openai_reasoning_effort is None


def test_factory_passes_effort_to_provider(monkeypatch) -> None:
    from baselith.core.services.llm.provider_factory import create_provider

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_OPENAI_REASONING_EFFORT", "none")
    provider = create_provider(LLMConfig())
    assert isinstance(provider, OpenAIProvider)
    assert provider._reasoning_effort == "none"


def test_reasoning_model_drops_non_default_temperature() -> None:
    out = request_kwargs({"temperature": 0.7}, None, "gpt-6-luna")
    assert "temperature" not in out
    assert request_kwargs({"temperature": 1}, None, "gpt-6-luna")["temperature"] == 1


def test_other_models_keep_their_temperature() -> None:
    assert (
        request_kwargs({"temperature": 0.7}, "none", "gpt-4o-mini")["temperature"]
        == 0.7
    )


def test_message_path_gets_effort_so_tools_are_accepted() -> None:
    from baselith.core.services.llm.messages import Message
    from baselith.core.services.llm.providers._openai_messages import build_request

    msgs = [Message.user("hi")]
    req = build_request(
        msgs,
        "gpt-6-luna",
        tools=None,
        system=None,
        kwargs={"temperature": 0.7},
        reasoning_effort="none",
    )
    assert req["reasoning_effort"] == "none"
    assert "temperature" not in req
    stream_req = build_request(
        msgs,
        "gpt-6-luna",
        tools=None,
        system=None,
        kwargs={"reasoning_effort": "none"},
    )
    assert stream_req["reasoning_effort"] == "none"


def test_message_path_never_sends_effort_to_a_non_reasoning_model() -> None:
    from baselith.core.services.llm.messages import Message
    from baselith.core.services.llm.providers._openai_messages import build_request

    req = build_request(
        [Message.user("hi")],
        "gpt-4o-mini",
        tools=None,
        system=None,
        kwargs={"reasoning_effort": "none"},
        reasoning_effort="none",
    )
    assert "reasoning_effort" not in req


async def _streamed_request(model: str, effort: str | None, **kwargs) -> dict:
    """The request the provider's streamed message turn sends (no network)."""
    from types import SimpleNamespace

    from baselith.core.services.llm.messages import Message
    from baselith.core.services.llm.tool_calling import LLMToolSpec

    seen: list[dict] = []

    class _Empty:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def close(self) -> None:
            return None

    async def create(**request):
        seen.append(request)
        return _Empty()

    provider = OpenAIProvider(api_key="x", reasoning_effort=effort)
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    tool = LLMToolSpec(name="t", description="d", parameters={"type": "object"})
    async for _ in provider.generate_messages_stream(
        [Message.user("hi")], model, tools=[tool], **kwargs
    ):
        pass
    return seen[0]


async def test_the_streamed_turn_carries_the_default_effort_with_tools() -> None:
    # Lith streams every turn with tools: a reasoning model answers 400 without "none".
    assert (await _streamed_request("gpt-6-luna", "none"))["reasoning_effort"] == "none"


async def test_the_streamed_turn_lets_the_caller_override_and_skips_old_models() -> (
    None
):
    assert (await _streamed_request("gpt-6-luna", "none", reasoning_effort="low"))[
        "reasoning_effort"
    ] == "low"
    assert "reasoning_effort" not in await _streamed_request("gpt-4o", "none")
    assert "reasoning_effort" not in await _streamed_request("gpt-6-luna", None)
