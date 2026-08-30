"""Tests for the group-chat coordination topology."""

from __future__ import annotations

import pytest
from core.agent.group_chat import (
    CapabilitySelector,
    ChatMessage,
    GroupChat,
    LLMManagerSelector,
    RoundRobinSelector,
)

from core.orchestration.limits import LoopBudget, LoopLimits

pytestmark = [pytest.mark.unit]


def _participant(name: str, reply: str = "", capabilities: tuple[str, ...] = ()):
    class _P:
        def __init__(self) -> None:
            self.name = name
            self.capabilities = list(capabilities)
            self.seen: list[str] = []

        async def respond(self, topic: str, transcript: list[ChatMessage]) -> str:
            self.seen.append(topic)
            return reply or f"{name} says round {len(transcript) + 1}"

    return _P()


class TestRoundRobin:
    async def test_rotates_speakers_in_order(self):
        a, b, c = _participant("a"), _participant("b"), _participant("c")
        chat = GroupChat([a, b, c], RoundRobinSelector(), max_rounds=5)
        result = await chat.run("plan the launch")
        speakers = [m.speaker for m in result.transcript]
        assert speakers == ["a", "b", "c", "a", "b"]
        assert result.rounds == 5
        assert result.terminated_by == "max_rounds"

    async def test_termination_predicate_stops_early(self):
        a = _participant("a", reply="DONE: shipped")
        b = _participant("b")
        chat = GroupChat(
            [a, b],
            RoundRobinSelector(),
            max_rounds=10,
            terminate=lambda transcript: "DONE:" in transcript[-1].content,
        )
        result = await chat.run("ship it")
        assert result.rounds == 1
        assert result.terminated_by == "predicate"

    async def test_budget_bounds_rounds(self):
        budget = LoopBudget(limits=LoopLimits(max_iterations=2))
        chat = GroupChat(
            [_participant("a"), _participant("b")],
            RoundRobinSelector(),
            max_rounds=10,
            budget=budget,
        )
        result = await chat.run("talk")
        assert result.rounds == 2
        assert result.terminated_by == "budget"

    async def test_requires_participants(self):
        with pytest.raises(ValueError):
            GroupChat([], RoundRobinSelector())


class TestLLMManagerSelector:
    async def test_manager_picks_named_speaker(self):
        class _LLM:
            async def generate_response(self, prompt: str, json: bool = False):
                import json as json_module

                return json_module.dumps({"reasoning": "b knows", "speaker": "b"})

        a, b = _participant("a"), _participant("b")
        selector = LLMManagerSelector(_LLM())
        chat = GroupChat([a, b], selector, max_rounds=1)
        result = await chat.run("topic")
        assert result.transcript[0].speaker == "b"

    async def test_malformed_manager_falls_back_to_round_robin(self):
        class _LLM:
            async def generate_response(self, prompt: str, json: bool = False):
                return "not json at all"

        a, b = _participant("a"), _participant("b")
        chat = GroupChat([a, b], LLMManagerSelector(_LLM()), max_rounds=2)
        result = await chat.run("topic")
        assert [m.speaker for m in result.transcript] == ["a", "b"]

    async def test_unknown_speaker_falls_back(self):
        class _LLM:
            async def generate_response(self, prompt: str, json: bool = False):
                return json.dumps({"reasoning": "?", "speaker": "nobody"})

        a, b = _participant("a"), _participant("b")
        result = await GroupChat([a, b], LLMManagerSelector(_LLM()), max_rounds=1).run(
            "t"
        )
        assert result.transcript[0].speaker == "a"


class TestCapabilitySelector:
    async def test_matches_last_message_against_capabilities(self):
        coder = _participant("coder", capabilities=("python", "debugging"))
        writer = _participant(
            "writer", reply="we need python debugging next", capabilities=("prose",)
        )
        chat = GroupChat([writer, coder], CapabilitySelector(), max_rounds=2)
        result = await chat.run("draft then fix the python bug")
        # First round: matched against the topic; second: against writer's reply.
        assert result.transcript[1].speaker == "coder"

    async def test_no_match_falls_back_to_round_robin(self):
        a = _participant("a", capabilities=("x",))
        b = _participant("b", capabilities=("y",))
        result = await GroupChat([a, b], CapabilitySelector(), max_rounds=2).run(
            "zzz qqq"
        )
        assert [m.speaker for m in result.transcript] == ["a", "b"]


class TestTranscript:
    async def test_participants_see_growing_transcript(self):
        seen_lengths: list[int] = []

        class _P:
            name = "obs"
            capabilities: list[str] = []

            async def respond(self, topic, transcript):
                seen_lengths.append(len(transcript))
                return "noted"

        await GroupChat([_P()], RoundRobinSelector(), max_rounds=3).run("t")
        assert seen_lengths == [0, 1, 2]
