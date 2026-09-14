"""Neutral message types and their provider wire mappings.

The agent loop's correctness rests on these: a tool result has to travel back
correlated to the call that produced it (``tool_use_id``), flagged when it
failed (``is_error``), and with the assistant turn that requested it replayed
verbatim — thinking blocks included, or the provider rejects the turn.
"""

from types import SimpleNamespace

from core.services.llm.messages import (
    ImageBlock,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    message_from_result,
    render_as_prompt,
    to_anthropic,
    to_openai,
)
from core.services.llm.tool_calling import LLMResult, ToolCall


class TestNeutralTypes:
    def test_user_helper_builds_one_text_block(self):
        message = Message.user("hello")
        assert message.role == "user"
        assert message.content == [TextBlock(text="hello")]
        assert message.text == "hello"

    def test_tool_results_helper_batches_into_one_user_message(self):
        message = Message.tool_results(
            [
                ToolResultBlock(tool_use_id="a", content="1"),
                ToolResultBlock(tool_use_id="b", content="2", is_error=True),
            ]
        )
        assert message.role == "user"
        assert len(message.content) == 2

    def test_tool_uses_exposes_the_calls_of_an_assistant_turn(self):
        message = Message(
            role="assistant",
            content=[
                TextBlock(text="let me look"),
                ToolUseBlock(id="t1", name="search", input={"q": "rome"}),
            ],
        )
        assert [b.name for b in message.tool_uses] == ["search"]


class TestAnthropicMapping:
    def test_text_and_tool_use_round_trip(self):
        wire = to_anthropic(
            [
                Message.user("population of Rome?"),
                Message(
                    role="assistant",
                    content=[ToolUseBlock(id="t1", name="pop", input={"city": "Rome"})],
                ),
            ]
        )
        assert wire[0] == {
            "role": "user",
            "content": [{"type": "text", "text": "population of Rome?"}],
        }
        assert wire[1]["content"][0] == {
            "type": "tool_use",
            "id": "t1",
            "name": "pop",
            "input": {"city": "Rome"},
        }

    def test_parallel_tool_results_stay_in_one_user_message(self):
        wire = to_anthropic(
            [
                Message.tool_results(
                    [
                        ToolResultBlock(tool_use_id="t1", content="a"),
                        ToolResultBlock(tool_use_id="t2", content="b", is_error=True),
                    ]
                )
            ]
        )
        assert len(wire) == 1
        assert wire[0]["role"] == "user"
        assert wire[0]["content"] == [
            {"type": "tool_result", "tool_use_id": "t1", "content": "a"},
            {
                "type": "tool_result",
                "tool_use_id": "t2",
                "content": "b",
                "is_error": True,
            },
        ]

    def test_thinking_block_is_replayed_verbatim(self):
        payload = {
            "type": "thinking",
            "thinking": "step one",
            "signature": "sig-abc",
        }
        wire = to_anthropic(
            [Message(role="assistant", content=[ThinkingBlock(payload=payload)])]
        )
        assert wire[0]["content"][0] == payload

    def test_image_block_maps_to_a_base64_source(self):
        wire = to_anthropic(
            [
                Message(
                    role="user",
                    content=[ImageBlock(data="Zm9v", media_type="image/png")],
                )
            ]
        )
        assert wire[0]["content"][0] == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "Zm9v"},
        }


class TestOpenAIMapping:
    def test_assistant_tool_calls_serialise_arguments_as_json(self):
        wire = to_openai(
            [
                Message(
                    role="assistant",
                    content=[
                        TextBlock(text="checking"),
                        ToolUseBlock(id="t1", name="pop", input={"city": "Rome"}),
                    ],
                )
            ]
        )
        assert wire[0]["role"] == "assistant"
        assert wire[0]["content"] == "checking"
        assert wire[0]["tool_calls"] == [
            {
                "id": "t1",
                "type": "function",
                "function": {"name": "pop", "arguments": '{"city": "Rome"}'},
            }
        ]

    def test_tool_results_expand_into_one_tool_message_each(self):
        wire = to_openai(
            [
                Message.tool_results(
                    [
                        ToolResultBlock(tool_use_id="t1", content="a"),
                        ToolResultBlock(tool_use_id="t2", content="b"),
                    ]
                )
            ]
        )
        assert [m["role"] for m in wire] == ["tool", "tool"]
        assert wire[0] == {"role": "tool", "tool_call_id": "t1", "content": "a"}

    def test_error_results_keep_the_failure_visible_without_an_is_error_field(self):
        """OpenAI tool messages carry no error flag; the text has to say so."""
        wire = to_openai(
            [
                Message.tool_results(
                    [ToolResultBlock(tool_use_id="t1", content="boom", is_error=True)]
                )
            ]
        )
        assert "is_error" not in wire[0]
        assert wire[0]["content"].startswith("Error")
        assert "boom" in wire[0]["content"]

    def test_a_result_that_merely_starts_with_the_word_errors_is_still_marked(self):
        """Matching the word "Error" loosely loses the flag on honest text."""
        wire = to_openai(
            [
                Message.tool_results(
                    [
                        ToolResultBlock(
                            tool_use_id="t1",
                            content="Errors were handled fine",
                            is_error=True,
                        )
                    ]
                )
            ]
        )
        assert wire[0]["content"] == "Error: Errors were handled fine"

    def test_an_already_prefixed_result_is_not_prefixed_twice(self):
        wire = to_openai(
            [
                Message.tool_results(
                    [
                        ToolResultBlock(
                            tool_use_id="t1", content="Error: boom", is_error=True
                        )
                    ]
                )
            ]
        )
        assert wire[0]["content"] == "Error: boom"

    def test_tool_call_arguments_serialise_with_sorted_keys(self):
        """A stable rendering is what lets a replayed turn hash identically."""
        wire = to_openai(
            [
                Message(
                    role="assistant",
                    content=[ToolUseBlock(id="t1", name="pop", input={"b": 2, "a": 1})],
                )
            ]
        )
        assert wire[0]["tool_calls"][0]["function"]["arguments"] == '{"a": 1, "b": 2}'

    def test_text_alongside_tool_results_is_not_dropped(self):
        """A neutral turn is a list of blocks, so it can carry results *and* a
        note. OpenAI's ``tool`` message has nowhere to put the note, and the
        mapping used to drop it — the model never saw an instruction the caller
        believed it had sent."""
        wire = to_openai(
            [
                Message(
                    role="user",
                    content=[
                        ToolResultBlock(tool_use_id="t1", content="42"),
                        TextBlock(text="now double it"),
                    ],
                )
            ]
        )
        assert [m["role"] for m in wire] == ["tool", "user"]
        assert wire[0] == {"role": "tool", "tool_call_id": "t1", "content": "42"}
        assert wire[1] == {"role": "user", "content": "now double it"}

    def test_an_image_alongside_tool_results_survives_too(self):
        wire = to_openai(
            [
                Message(
                    role="user",
                    content=[
                        ToolResultBlock(tool_use_id="t1", content="42"),
                        ImageBlock(media_type="image/png", data="Zm9v"),
                    ],
                )
            ]
        )
        assert [m["role"] for m in wire] == ["tool", "user"]
        assert wire[1]["content"][0]["type"] == "image_url"

    def test_results_only_still_emit_no_trailing_user_turn(self):
        """The common shape is unchanged: no empty user message appended."""
        wire = to_openai(
            [Message.tool_results([ToolResultBlock(tool_use_id="t1", content="a")])]
        )
        assert [m["role"] for m in wire] == ["tool"]

    def test_thinking_blocks_are_dropped(self):
        wire = to_openai(
            [
                Message(
                    role="assistant",
                    content=[ThinkingBlock(payload={"type": "thinking"})],
                )
            ]
        )
        assert wire == [{"role": "assistant", "content": ""}]


class TestMessageFromResult:
    def test_explicit_message_wins(self):
        message = Message.assistant("done")
        result = LLMResult(text="ignored", message=message)
        assert message_from_result(result) is message

    def test_anthropic_raw_blocks_keep_thinking(self):
        raw = SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking="hmm", signature="sig"),
                SimpleNamespace(type="text", text="answer"),
            ]
        )
        message = message_from_result(LLMResult(text="answer", raw=raw))
        kinds = [type(block).__name__ for block in message.content]
        assert kinds == ["ThinkingBlock", "TextBlock"]
        assert message.content[0].payload["signature"] == "sig"

    def test_falls_back_to_text_and_tool_calls(self):
        result = LLMResult(
            text="hi",
            tool_calls=[ToolCall(id="t1", name="pop", arguments={"city": "Rome"})],
        )
        message = message_from_result(result)
        assert message.role == "assistant"
        assert message.text == "hi"
        assert message.tool_uses[0].id == "t1"


class TestPromptRendering:
    def test_history_flattens_for_providers_without_a_message_api(self):
        rendered = render_as_prompt(
            [
                Message.user("population of Rome?"),
                Message(
                    role="assistant",
                    content=[ToolUseBlock(id="t1", name="pop", input={"city": "Rome"})],
                ),
                Message.tool_results(
                    [ToolResultBlock(tool_use_id="t1", content="2870000")]
                ),
            ]
        )
        assert "population of Rome?" in rendered
        assert "pop" in rendered
        assert "2870000" in rendered
