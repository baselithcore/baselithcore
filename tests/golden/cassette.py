"""Recorded-LLM cassettes for golden trajectory tests.

A *cassette* is an ordered list of provider turns captured from a real run
(or authored by hand). :class:`RecordedLLMService` replays it into the real
``Agent`` loop in place of ``LLMService``: every call the loop makes is
matched against the turn's ``expect`` block — which tools were offered, what
the assembled prompt must contain, the structured-output schema — and any
drift raises :class:`CassetteMismatch`. That is what makes the test *golden*
rather than a stub: a regression in prompt assembly, tool-result feedback or
retry wording fails loudly instead of being absorbed by an ``AsyncMock``.

:class:`RecordingLLMService` wraps a live service and writes a cassette,
so a trajectory can be captured once against a real provider and replayed
forever without keys, cost or network.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.services.llm.tool_calling import LLMResult, ToolCall

CASSETTE_DIR = Path(__file__).resolve().parent / "cassettes"


class CassetteMismatch(AssertionError):
    """The agent loop called the provider differently than the cassette expects."""


@dataclass
class Expect:
    """What a turn asserts about the call it answers. Every field is optional."""

    prompt_contains: list[str] = field(default_factory=list)
    system_prompt_contains: list[str] = field(default_factory=list)
    tools: list[str] | None = None
    response_format: str | None = None

    def check(self, index: int, **call: Any) -> None:
        prompt = call.get("prompt") or ""
        for needle in self.prompt_contains:
            if needle not in prompt:
                raise CassetteMismatch(
                    f"turn {index}: prompt does not contain {needle!r}\n--- prompt ---\n{prompt}"
                )
        system_prompt = call.get("system_prompt") or ""
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
            "expect": {
                k: v for k, v in self.expect.__dict__.items() if v not in (None, [])
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
    """Replays a cassette through the ``LLMService`` surface the Agent loop uses."""

    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette
        self._index = 0
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self, prompt: str, model: str | None = None, **kwargs: Any
    ) -> LLMResult:
        index = self._index
        if index >= len(self.cassette.turns):
            raise CassetteMismatch(
                f"turn {index}: the agent made more provider calls than the cassette "
                f"{self.cassette.name!r} holds ({len(self.cassette.turns)})"
            )
        turn = self.cassette.turns[index]
        self._index += 1
        call = {"prompt": prompt, "model": model, **kwargs}
        turn.expect.check(index, **call)
        self.calls.append(call)
        return turn.result

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

    async def generate(
        self, prompt: str, model: str | None = None, **kwargs: Any
    ) -> LLMResult:
        result: LLMResult = await self._inner.generate(prompt, model, **kwargs)
        fmt = kwargs.get("response_format")
        expect = Expect(
            tools=sorted(spec.name for spec in (kwargs.get("tools") or [])) or None,
            response_format=getattr(fmt, "name", None),
        )
        turn = Turn(result=result, expect=expect)
        turn.expect.prompt_contains = []
        self.cassette.turns.append(turn)
        return result

    async def generate_response(
        self, prompt: str, model: str | None = None, **kwargs: Any
    ) -> str:
        return (await self.generate(prompt, model, **kwargs)).text or ""

    def save(self, directory: Path = CASSETTE_DIR) -> Path:
        return self.cassette.save(directory)
