"""Recorded-LLM cassettes: replay a provider script through the real loop.

A *cassette* is an ordered list of provider turns captured from a real run
(or authored by hand). :class:`RecordedLLMService` replays it into the real
agent loop in place of ``LLMService``: every call the loop makes is matched
against the turn's ``expect`` block — which tools were offered, what the
assembled conversation must contain, the structured-output schema — and any
drift raises :class:`CassetteMismatch`. That is what makes a replay *golden*
rather than a stub: a regression in prompt assembly, tool-result feedback or
retry wording fails loudly instead of being absorbed by an ``AsyncMock``.

:class:`RecordingLLMService` wraps a live service and writes a cassette, so a
trajectory can be captured once against a real provider and replayed forever
without keys, cost or network.

This lives in ``core`` rather than in the test tree because it has two
consumers with the same need. The golden unit tests pin the loop's wire
contract with it; :mod:`core.evaluation.replay` drives the eval regression
gate with it, so that gate exercises the real agent instead of grading
hand-written fixtures against hand-written expectations.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.orchestration.tool_output import UNTRUSTED_OPEN_PREFIX
from core.services.llm.messages import Message, ToolResultBlock, render_as_prompt
from core.services.llm.tool_calling import LLMResult, ToolCall

#: Where the golden unit-test cassettes live. Callers with their own
#: library (the eval gate) pass ``directory=`` explicitly.
CASSETTE_DIR = Path(__file__).resolve().parents[2] / "tests" / "golden" / "cassettes"


class CassetteMismatch(AssertionError):
    """The agent loop called the provider differently than the cassette expects."""


@dataclass
class Expect:
    """What a turn asserts about the call it answers. Every field is optional.

    The loop sends a **message history**, not a prompt, so the shape fields
    (:attr:`roles`, :attr:`tool_results`, :attr:`envelope`) are the ones that
    pin the wire contract; :attr:`prompt_contains` still works and is checked
    against the history rendered as a transcript, which is also exactly what a
    service without the message API receives.

    Attributes:
        prompt_contains: Substrings that must appear in the sent conversation.
        system_prompt_contains: Substrings of the system prompt.
        tools: Exact set of tool names offered.
        response_format: Expected structured-output schema name.
        roles: Exact role sequence of the history sent, oldest first. This is
            what catches a loop that rebuilds the conversation instead of
            appending to it.
        tool_results: One entry per ``tool_result`` block in the final message,
            in order — ``{"tool_use_id", "contains", "is_error"}``. A turn that
            answers several tool calls must carry them in ONE message, so the
            length of this list is itself an assertion.
        envelope: When True, every tool-result body must be sealed in the
            untrusted-content envelope.
    """

    prompt_contains: list[str] = field(default_factory=list)
    system_prompt_contains: list[str] = field(default_factory=list)
    tools: list[str] | None = None
    response_format: str | None = None
    roles: list[str] | None = None
    tool_results: list[dict[str, Any]] | None = None
    envelope: bool = False

    def check(self, index: int, **call: Any) -> None:
        messages: list[Message] | None = call.get("messages")
        prompt = call.get("prompt") or (render_as_prompt(messages) if messages else "")
        for needle in self.prompt_contains:
            if needle not in prompt:
                raise CassetteMismatch(
                    f"turn {index}: prompt does not contain {needle!r}\n--- prompt ---\n{prompt}"
                )
        self._check_shape(index, messages)
        system_prompt = call.get("system") or call.get("system_prompt") or ""
        for needle in self.system_prompt_contains:
            if needle not in system_prompt:
                raise CassetteMismatch(f"turn {index}: system prompt lacks {needle!r}")
        if self.tools is not None:
            offered = sorted(spec.name for spec in (call.get("tools") or []))
            if offered != sorted(self.tools):
                raise CassetteMismatch(
                    f"turn {index}: tools offered {offered} != expected {sorted(self.tools)}"
                )
        if self.response_format is not None:
            fmt = call.get("response_format")
            actual = getattr(fmt, "name", None)
            if actual != self.response_format:
                raise CassetteMismatch(
                    f"turn {index}: response_format {actual!r} != {self.response_format!r}"
                )

    def _check_shape(self, index: int, messages: list[Message] | None) -> None:
        """Assert the message-level contract of the conversation sent."""
        if self.roles is not None:
            if messages is None:
                raise CassetteMismatch(
                    f"turn {index}: expected a message history, got a flat prompt"
                )
            actual = [m.role for m in messages]
            if actual != self.roles:
                raise CassetteMismatch(
                    f"turn {index}: message roles {actual} != expected {self.roles}"
                )
        if self.tool_results is None and not self.envelope:
            return
        if not messages:
            raise CassetteMismatch(
                f"turn {index}: expected tool results, got no message history"
            )
        blocks = [b for b in messages[-1].content if isinstance(b, ToolResultBlock)]
        if self.tool_results is not None and len(blocks) != len(self.tool_results):
            raise CassetteMismatch(
                f"turn {index}: the final message carries {len(blocks)} tool "
                f"result(s), expected {len(self.tool_results)} — every result of "
                f"one turn belongs in a single message"
            )
        for position, expected in enumerate(self.tool_results or []):
            self._check_result(index, position, blocks[position], expected)
        if self.envelope:
            for block in blocks:
                if not block.content.startswith(UNTRUSTED_OPEN_PREFIX):
                    raise CassetteMismatch(
                        f"turn {index}: tool result {block.tool_use_id!r} is not "
                        f"sealed in the untrusted envelope: {block.content[:80]!r}"
                    )

    @staticmethod
    def _check_result(
        index: int, position: int, block: ToolResultBlock, expected: dict[str, Any]
    ) -> None:
        """Assert one ``tool_result`` block against its recorded expectation."""
        where = f"turn {index} tool_result[{position}]"
        wanted_id = expected.get("tool_use_id")
        if wanted_id is not None and block.tool_use_id != wanted_id:
            raise CassetteMismatch(
                f"{where}: tool_use_id {block.tool_use_id!r} != {wanted_id!r} — "
                f"the result is not correlated to the call that produced it"
            )
        for needle in expected.get("contains", []):
            if needle not in block.content:
                raise CassetteMismatch(
                    f"{where}: content lacks {needle!r}\n--- content ---\n{block.content}"
                )
        wanted_error = expected.get("is_error")
        if wanted_error is not None and block.is_error is not wanted_error:
            raise CassetteMismatch(
                f"{where}: is_error {block.is_error} != {wanted_error}"
            )


@dataclass
class Turn:
    """One provider round-trip: what was asked (``expect``) and what came back."""

    result: LLMResult
    expect: Expect = field(default_factory=Expect)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Turn:
        res = raw.get("result", {})
        calls = [
            ToolCall(id=c["id"], name=c["name"], arguments=dict(c.get("arguments", {})))
            for c in res.get("tool_calls", [])
        ]
        return cls(
            result=LLMResult(
                text=res.get("text"),
                tool_calls=calls,
                stop_reason=res.get("stop_reason"),
                tokens_used=int(res.get("tokens_used", 0)),
            ),
            expect=Expect(**raw.get("expect", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            # ``envelope`` is a bool, so it needs an explicit falsy check that
            # ``v not in (None, [])`` does not give it (False == 0 passes).
            "expect": {
                k: v
                for k, v in self.expect.__dict__.items()
                if v not in (None, []) and not (k == "envelope" and v is False)
            },
            "result": {
                "text": self.result.text,
                "tool_calls": [
                    {"id": c.id, "name": c.name, "arguments": c.arguments}
                    for c in self.result.tool_calls
                ],
                "stop_reason": self.result.stop_reason,
                "tokens_used": self.result.tokens_used,
            },
        }


@dataclass
class Cassette:
    """A named, ordered list of turns."""

    name: str
    turns: list[Turn]
    description: str = ""

    @classmethod
    def load(cls, name: str, directory: Path = CASSETTE_DIR) -> Cassette:
        raw = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
        return cls(
            name=raw.get("name", name),
            description=raw.get("description", ""),
            turns=[Turn.from_dict(t) for t in raw["turns"]],
        )

    def save(self, directory: Path = CASSETTE_DIR) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.name}.json"
        payload = {
            "name": self.name,
            "description": self.description,
            "turns": [t.to_dict() for t in self.turns],
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path


class RecordedLLMService:
    """Replays a cassette through the ``LLMService`` surface the Agent loop uses.

    Speaks the **message API** by default, because that is what the loop uses
    against a real service: ``supports_messages`` is the flag ``Agent._generate``
    reads, and a replay that left it off would quietly pin the legacy transcript
    path instead of the one production takes. Construct with
    ``supports_messages=False`` to pin that legacy path deliberately — it is
    still live for services that predate the message API.
    """

    def __init__(self, cassette: Cassette, *, supports_messages: bool = True) -> None:
        self.cassette = cassette
        self.supports_messages = supports_messages
        self._index = 0
        self.calls: list[dict[str, Any]] = []

    def _next(self, call: dict[str, Any]) -> LLMResult:
        index = self._index
        if index >= len(self.cassette.turns):
            raise CassetteMismatch(
                f"turn {index}: the agent made more provider calls than the cassette "
                f"{self.cassette.name!r} holds ({len(self.cassette.turns)})"
            )
        turn = self.cassette.turns[index]
        self._index += 1
        turn.expect.check(index, **call)
        self.calls.append(call)
        return turn.result

    async def generate_messages(
        self, messages: list[Message], **kwargs: Any
    ) -> LLMResult:
        """The message-based call: the history is the unit of assertion."""
        return self._next({"messages": messages, **kwargs})

    async def generate(
        self, prompt: str, model: str | None = None, **kwargs: Any
    ) -> LLMResult:
        return self._next({"prompt": prompt, "model": model, **kwargs})

    async def generate_response(
        self, prompt: str, model: str | None = None, **kwargs: Any
    ) -> str:
        result = await self.generate(prompt, model, **kwargs)
        return result.text or ""

    async def generate_response_stream(
        self, prompt: str, model: str | None = None, **kwargs: Any
    ) -> AsyncIterator[str]:
        text = await self.generate_response(prompt, model, **kwargs)
        for start in range(0, len(text), 8):
            yield text[start : start + 8]

    def assert_exhausted(self) -> None:
        """Fail when the loop finished without playing every recorded turn."""
        remaining = len(self.cassette.turns) - self._index
        if remaining:
            raise AssertionError(
                f"cassette {self.cassette.name!r}: {remaining} recorded turn(s) never played"
            )


class RecordingLLMService:
    """Wraps a live ``LLMService`` and captures every call as a cassette turn.

    The recorded ``expect`` block carries the offered tools and the
    structured-output name verbatim; ``prompt_contains`` is left for the author
    to curate from ``prompt_excerpt`` so the assertion names what matters.
    """

    def __init__(self, inner: Any, name: str, description: str = "") -> None:
        self._inner = inner
        self.cassette = Cassette(name=name, turns=[], description=description)

    #: Mirrors the replay service so a recording run takes the same path.
    supports_messages: bool = True

    def _capture(
        self, result: LLMResult, messages: list[Message] | None, **kwargs: Any
    ) -> None:
        """Append one turn, with the shape fields filled in from the live call."""
        fmt = kwargs.get("response_format")
        expect = Expect(
            tools=sorted(spec.name for spec in (kwargs.get("tools") or [])) or None,
            response_format=getattr(fmt, "name", None),
        )
        if messages is not None:
            expect.roles = [m.role for m in messages]
            blocks = [b for b in messages[-1].content if isinstance(b, ToolResultBlock)]
            if blocks:
                # ``contains`` is left empty for the author to curate: the
                # recorder knows the shape, only a human knows which substring
                # of a tool result is the one worth pinning forever.
                expect.tool_results = [
                    {
                        "tool_use_id": b.tool_use_id,
                        "is_error": b.is_error,
                        "contains": [],
                    }
                    for b in blocks
                ]
                expect.envelope = True
        self.cassette.turns.append(Turn(result=result, expect=expect))

    async def generate_messages(
        self, messages: list[Message], **kwargs: Any
    ) -> LLMResult:
        result: LLMResult = await self._inner.generate_messages(messages, **kwargs)
        self._capture(result, list(messages), **kwargs)
        return result

    async def generate(
        self, prompt: str, model: str | None = None, **kwargs: Any
    ) -> LLMResult:
        result: LLMResult = await self._inner.generate(prompt, model, **kwargs)
        self._capture(result, None, **kwargs)
        return result

    async def generate_response(
        self, prompt: str, model: str | None = None, **kwargs: Any
    ) -> str:
        return (await self.generate(prompt, model, **kwargs)).text or ""

    def save(self, directory: Path = CASSETTE_DIR) -> Path:
        return self.cassette.save(directory)
