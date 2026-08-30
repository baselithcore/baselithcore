"""Group chat: free-form multi-agent conversation with speaker selection.

The collaboration topologies shipped so far are structured — ``Crew`` is a
task DAG, the swarm is a task market. Group chat is the emergent third
shape: participants share one growing transcript and a *speaker selector*
decides who talks next, so coordination arises from the conversation
itself rather than a pre-planned graph.

Selectors: :class:`RoundRobinSelector` (deterministic rotation),
:class:`LLMManagerSelector` (a manager model reads the transcript and
names the next speaker — malformed or unknown answers fall back to
round-robin, never crash the chat), and :class:`CapabilitySelector`
(keyword match of the last message against participant capabilities).

Every chat is bounded three ways: ``max_rounds``, an optional caller
``terminate`` predicate over the transcript, and an optional
:class:`~core.orchestration.limits.LoopBudget` ticked once per round —
an emergent conversation is still a loop, and loops end.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from core.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ChatMessage:
    """One utterance on the shared transcript."""

    speaker: str
    content: str


@runtime_checkable
class Participant(Protocol):
    """A group-chat member.

    ``capabilities`` may be empty; it only feeds
    :class:`CapabilitySelector`. Adapt a
    :class:`~core.agent.agent.Agent` by wrapping ``agent.run`` in
    ``respond`` and returning ``result.text``.
    """

    name: str
    capabilities: list[str]

    async def respond(self, topic: str, transcript: list[ChatMessage]) -> str: ...


@runtime_checkable
class SpeakerSelector(Protocol):
    """Chooses who speaks next given the topic and transcript so far."""

    async def select(
        self,
        participants: Sequence[Participant],
        topic: str,
        transcript: list[ChatMessage],
    ) -> Participant: ...


class RoundRobinSelector:
    """Deterministic rotation in registration order."""

    async def select(
        self,
        participants: Sequence[Participant],
        topic: str,
        transcript: list[ChatMessage],
    ) -> Participant:
        return participants[len(transcript) % len(participants)]


class LLMManagerSelector:
    """A manager model names the next speaker (fail-open to round-robin).

    The manager sees the roster (name + capabilities), the topic, and the
    transcript tail, and must answer strict JSON with its reasoning first.
    Any failure — LLM error, malformed JSON, unknown name — degrades to
    the round-robin choice with a logged warning: a flaky manager slows
    the conversation down, it never ends it.
    """

    def __init__(self, llm_service: Any, *, transcript_tail: int = 10) -> None:
        self._llm = llm_service
        self._tail = transcript_tail
        self._fallback = RoundRobinSelector()

    async def select(
        self,
        participants: Sequence[Participant],
        topic: str,
        transcript: list[ChatMessage],
    ) -> Participant:
        roster = "\n".join(
            f"- {p.name}: {', '.join(p.capabilities) or 'generalist'}"
            for p in participants
        )
        tail = "\n".join(f"{m.speaker}: {m.content}" for m in transcript[-self._tail :])
        prompt = f"""You are moderating a working conversation. Pick who should speak next.

TOPIC: {topic}

PARTICIPANTS:
{roster}

TRANSCRIPT (most recent last):
{tail or "(not started)"}

Write your reasoning FIRST, then answer with JSON in this exact key order:
{{
    "reasoning": "<why this participant moves the conversation forward>",
    "speaker": "<exact participant name>"
}}"""
        try:
            raw = await self._llm.generate_response(prompt, json=True)
            name = str(json.loads(raw)["speaker"])
            for participant in participants:
                if participant.name == name:
                    return participant
            logger.warning("group_chat_manager_unknown_speaker name=%s", name)
        except Exception as exc:
            logger.warning("group_chat_manager_failed error=%s", exc)
        return await self._fallback.select(participants, topic, transcript)


class CapabilitySelector:
    """Keyword match of the last message against participant capabilities.

    The participant whose capability tokens overlap the most with the last
    utterance (the topic, before anyone spoke) speaks next; no overlap
    anywhere falls back to round-robin.
    """

    def __init__(self) -> None:
        self._fallback = RoundRobinSelector()

    async def select(
        self,
        participants: Sequence[Participant],
        topic: str,
        transcript: list[ChatMessage],
    ) -> Participant:
        last = transcript[-1].content if transcript else topic
        words = {token.strip(".,!?;:").lower() for token in last.split()}
        best: Participant | None = None
        best_score = 0
        for participant in participants:
            score = sum(
                1
                for capability in participant.capabilities
                if capability.lower() in words
            )
            if score > best_score:
                best, best_score = participant, score
        if best is not None:
            return best
        return await self._fallback.select(participants, topic, transcript)


@dataclass
class GroupChatResult:
    """Terminal state of one conversation."""

    transcript: list[ChatMessage] = field(default_factory=list)
    rounds: int = 0
    terminated_by: str = "max_rounds"


class GroupChat:
    """Run a bounded multi-agent conversation.

    Args:
        participants: The roster (at least one).
        selector: Speaker-selection strategy.
        max_rounds: Hard cap on utterances.
        terminate: Optional predicate over the transcript, checked after
            every utterance; True ends the chat (``terminated_by:
            "predicate"``).
        budget: Optional ``LoopBudget`` ticked once per round; exhaustion
            ends the chat cleanly (``terminated_by: "budget"``) rather
            than raising.
    """

    def __init__(
        self,
        participants: Sequence[Participant],
        selector: SpeakerSelector,
        *,
        max_rounds: int = 8,
        terminate: Callable[[list[ChatMessage]], bool] | None = None,
        budget: Any | None = None,
    ) -> None:
        if not participants:
            raise ValueError("group chat needs at least one participant")
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        self._participants = list(participants)
        self._selector = selector
        self._max_rounds = max_rounds
        self._terminate = terminate
        self._budget = budget

    async def run(self, topic: str) -> GroupChatResult:
        """Converse about ``topic`` until a bound or the predicate ends it."""
        from core.orchestration.limits import BudgetExceededError

        result = GroupChatResult()
        for _ in range(self._max_rounds):
            if self._budget is not None:
                try:
                    self._budget.tick()
                except BudgetExceededError:
                    result.terminated_by = "budget"
                    return result
            speaker = await self._selector.select(
                self._participants, topic, result.transcript
            )
            content = await speaker.respond(topic, list(result.transcript))
            result.transcript.append(ChatMessage(speaker=speaker.name, content=content))
            result.rounds += 1
            if self._terminate is not None and self._terminate(result.transcript):
                result.terminated_by = "predicate"
                return result
        result.terminated_by = "max_rounds"
        return result


__all__ = [
    "CapabilitySelector",
    "ChatMessage",
    "GroupChat",
    "GroupChatResult",
    "LLMManagerSelector",
    "Participant",
    "RoundRobinSelector",
    "SpeakerSelector",
]
