"""process_stream must deliver real answers, never a placeholder string.

Before this suite, an intent without a registered StreamHandler made
``process_stream`` yield the literal ``"[INFO] Processing <intent>..."`` and
stop — the LLM answer never reached the HTTP stream. The contract now:

* no stream handler ⇒ fall back to the non-streaming ``process()`` and yield
  its (already output-guarded) final response as a single chunk;
* a registered stream handler's chunks pass through the streaming output
  guard (PII redaction across chunk boundaries).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from core.orchestration.orchestrator import Orchestrator


class _FlowHandler:
    def __init__(self, response: str = "real answer") -> None:
        self.response = response
        self.calls = 0

    async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {"response": self.response}


class _StreamHandler:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    async def handle(self, query: str, context: dict[str, Any]) -> AsyncIterator[str]:
        for chunk in self.chunks:
            yield chunk


def _forced_intent(orch: Orchestrator, monkeypatch, intent: str) -> None:
    async def fake_classify(query: str) -> str:
        return intent

    monkeypatch.setattr(orch, "classify_intent_async", fake_classify)


@pytest.mark.asyncio
async def test_fallback_runs_flow_handler_and_yields_real_response(monkeypatch):
    orch = Orchestrator()
    flow = _FlowHandler("the actual LLM answer")
    orch.register_handler("no_stream_intent", flow)
    _forced_intent(orch, monkeypatch, "no_stream_intent")

    chunks = [c async for c in orch.process_stream("hello")]

    joined = "".join(chunks)
    assert "the actual LLM answer" in joined
    assert flow.calls == 1
    assert not any("[INFO] Processing" in c for c in chunks)


@pytest.mark.asyncio
async def test_fallback_without_any_handler_reports_missing_handler(monkeypatch):
    orch = Orchestrator()
    _forced_intent(orch, monkeypatch, "ghost_intent")

    chunks = [c async for c in orch.process_stream("hello")]

    joined = "".join(chunks)
    assert "ghost_intent" in joined
    assert not any("[INFO] Processing" in c for c in chunks)


@pytest.mark.asyncio
async def test_stream_handler_chunks_pass_output_guard(monkeypatch):
    orch = Orchestrator()
    orch.register_handler(
        "leaky_intent",
        _FlowHandler(),
        stream_handler=_StreamHandler(["contact leak@exam", "ple.com for keys"]),
    )
    _forced_intent(orch, monkeypatch, "leaky_intent")

    chunks = [c async for c in orch.process_stream("hello")]

    assert "leak@example.com" not in "".join(chunks)


@pytest.mark.asyncio
async def test_stream_handler_safe_chunks_flow_through(monkeypatch):
    orch = Orchestrator()
    orch.register_handler(
        "safe_intent",
        _FlowHandler(),
        stream_handler=_StreamHandler(["Hello ", "streaming ", "world"]),
    )
    _forced_intent(orch, monkeypatch, "safe_intent")

    chunks = [c async for c in orch.process_stream("hello")]

    assert "".join(chunks) == "Hello streaming world"
