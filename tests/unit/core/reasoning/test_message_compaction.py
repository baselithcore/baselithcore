"""Message-history compaction: idempotent, structure-preserving, honest about size.

A message loop cannot be bounded the way a transcript can. Dropping a turn
breaks the ``tool_use`` / ``tool_result`` pairing a provider validates, so
:func:`compact_message_history` shortens block *contents* instead and never
removes anything. Three properties have to hold for that to be safe, and each
one failed in an early draft:

* **Idempotence.** ``compact_history`` gets it free by testing the marker on
  the whole entry. Block-level compaction has to test each block, or a long
  run re-compacts the same excerpt every pass, stacking markers and eating
  real content a dozen characters at a time — while ``total -= saved`` with a
  negative saving pushes the running estimate the wrong way.
* **Structure.** Ids, pairings and thinking payloads must survive untouched.
* **An honest size estimate.** A block whose weight is not counted cannot pull
  a history over budget, so it is never compacted — which is exactly the
  history that needed it.
"""

from __future__ import annotations

import random

import pytest

from core.reasoning.history import compact_message_history
from core.services.llm.messages import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

MARKER = "[compacted]"


def _texts(history: list[Message]) -> list[str]:
    out = []
    for message in history:
        for block in message.content:
            value = getattr(block, "text", None)
            if value is None:
                value = getattr(block, "content", None)
            if isinstance(value, str):
                out.append(value)
    return out


def _long(seed: int = 0, size: int = 900) -> str:
    """High-entropy text: the case where re-compaction used to *grow*."""
    random.seed(seed)
    return "".join(random.choices("ab c{}<>/_1", k=size))


class TestIdempotence:
    def test_a_second_pass_changes_nothing(self) -> None:
        history = [
            Message.user("task"),
            Message.assistant(_long(1)),
            Message.tool_results([ToolResultBlock(tool_use_id="t1", content=_long(2))]),
            Message.user("go on"),
        ]

        once = compact_message_history(history, max_tokens=20, keep_recent=1)
        twice = compact_message_history(once, max_tokens=20, keep_recent=1)

        assert _texts(twice) == _texts(once)

    @pytest.mark.parametrize("seed", range(8))
    def test_repeated_passes_never_stack_markers(self, seed: int) -> None:
        history = [Message.user("task")]
        for index in range(6):
            history.append(Message.assistant(_long(seed * 10 + index)))
            history.append(
                Message.tool_results(
                    [ToolResultBlock(tool_use_id=f"t{index}", content=_long(index))]
                )
            )

        for _ in range(6):
            history = compact_message_history(history, max_tokens=50, keep_recent=1)

        assert all(text.count(MARKER) <= 1 for text in _texts(history))


class TestStructureSurvives:
    def test_ids_and_pairings_are_untouched(self) -> None:
        history = [
            Message.user("task"),
            Message(
                role="assistant",
                content=[
                    TextBlock(_long(3)),
                    ToolUseBlock(id="tu_1", name="lookup", input={"q": _long(4, 400)}),
                ],
            ),
            Message.tool_results(
                [ToolResultBlock(tool_use_id="tu_1", content=_long(5))]
            ),
            Message.user("go on"),
        ]

        out = compact_message_history(history, max_tokens=20, keep_recent=1)

        uses = [b for m in out for b in m.content if isinstance(b, ToolUseBlock)]
        results = [b for m in out for b in m.content if isinstance(b, ToolResultBlock)]
        assert [u.id for u in uses] == ["tu_1"]
        assert [r.tool_use_id for r in results] == ["tu_1"]
        assert uses[0].input == history[1].content[1].input, "arguments were rewritten"

    def test_a_turn_with_thinking_is_left_whole(self) -> None:
        """Its signature is validated on replay; editing near it risks a 400."""
        thinking = Message(
            role="assistant",
            content=[
                ThinkingBlock(payload={"type": "thinking", "signature": "sig"}),
                TextBlock(_long(6)),
            ],
        )
        history = [Message.user("task"), thinking, Message.user("go on")]

        out = compact_message_history(history, max_tokens=20, keep_recent=1)

        assert out[1].content[1].text == thinking.content[1].text
        assert out[1].content[0].payload == {"type": "thinking", "signature": "sig"}

    def test_the_task_is_never_compacted(self) -> None:
        history = [
            Message.user(_long(7)),
            Message.assistant(_long(8)),
            Message.user("go on"),
        ]

        out = compact_message_history(history, max_tokens=20, keep_recent=1)

        assert out[0].content[0].text == history[0].content[0].text

    def test_the_input_history_is_not_mutated(self) -> None:
        original = _long(9)
        history = [
            Message.user("task"),
            Message.assistant(original),
            Message.user("go on"),
        ]

        compact_message_history(history, max_tokens=20, keep_recent=1)

        assert history[1].content[0].text == original


class TestSizeEstimate:
    def test_tool_arguments_count_toward_the_budget(self) -> None:
        """A history heavy with call arguments used to measure as empty."""
        history = [
            Message.user("task"),
            Message(
                role="assistant",
                content=[
                    TextBlock(_long(10)),
                    ToolUseBlock(id="tu_1", name="lookup", input={"q": "x" * 5000}),
                ],
            ),
            Message.user("go on"),
        ]

        out = compact_message_history(history, max_tokens=200, keep_recent=1)

        assert out[1].content[0].text.startswith(MARKER), "nothing was compacted"

    def test_a_history_under_budget_is_returned_unchanged(self) -> None:
        history = [Message.user("task"), Message.assistant("short"), Message.user("go")]

        out = compact_message_history(history, max_tokens=10_000)

        assert _texts(out) == ["task", "short", "go"]

    def test_a_zero_budget_disables_compaction(self) -> None:
        history = [
            Message.user("task"),
            Message.assistant(_long(11)),
            Message.user("g"),
        ]

        out = compact_message_history(history, max_tokens=0, keep_recent=1)

        assert _texts(out) == _texts(history)
