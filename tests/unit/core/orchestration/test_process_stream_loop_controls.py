"""``process_stream`` runs under the same per-request controls as ``process``.

Regression: the streaming path bound no ``LoopBudget`` (so token/USD caps and
the per-chunk deadline never applied), skipped the tenant guard and memory
recall, and never wrote the exchange to memory. ``/chat/stream`` was the one
door into the loop with none of its controls.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from core.orchestration.budget_context import get_active_budget
from core.orchestration.limits import LoopBudget, LoopLimits
from core.orchestration.orchestrator import Orchestrator


class _RecordingStream:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.seen_budget: Any = None
        self.ambient_budget: Any = None

    async def handle(self, query: str, context: dict[str, Any]) -> AsyncIterator[str]:
        self.seen_budget = context.get("loop_budget")
        self.ambient_budget = get_active_budget()
        for chunk in self.chunks:
            yield chunk


class _TickingStream:
    """Ticks the budget per chunk, as a multi-step streaming agent would."""

    async def handle(self, query: str, context: dict[str, Any]) -> AsyncIterator[str]:
        budget: LoopBudget = context["loop_budget"]
        for chunk in ("one ", "two ", "three"):
            budget.tick()
            yield chunk


def _orchestrator(
    monkeypatch, intent: str, handler: Any, **kwargs: Any
) -> Orchestrator:
    orch = Orchestrator(**kwargs)
    orch._stream_handlers[intent] = handler

    async def fake_classify(query: str) -> str:
        return intent

    monkeypatch.setattr(orch, "classify_intent_async", fake_classify)
    return orch


@pytest.mark.asyncio
async def test_budget_is_bound_for_the_stream_and_released_after(monkeypatch):
    handler = _RecordingStream(["a", "b"])
    orch = _orchestrator(monkeypatch, "s", handler)

    chunks = [c async for c in orch.process_stream("q")]

    assert "".join(chunks) == "ab"
    assert isinstance(handler.seen_budget, LoopBudget)
    assert handler.ambient_budget is handler.seen_budget
    assert get_active_budget() is None


@pytest.mark.asyncio
async def test_budget_breach_mid_stream_ends_with_the_refusal(monkeypatch):
    orch = _orchestrator(
        monkeypatch,
        "s",
        _TickingStream(),
        loop_limits=LoopLimits(max_iterations=2),
    )

    chunks = [c async for c in orch.process_stream("q")]

    # Text still inside the output guard's holdback window when the budget
    # trips is not flushed; the stream ends with the refusal, never "three".
    assert chunks[-1] == "Request aborted: max_iterations"
    assert "three" not in "".join(chunks)


@pytest.mark.asyncio
async def test_completed_stream_is_written_to_memory(monkeypatch):
    writes: list[tuple[str, str, str | None]] = []
    orch = _orchestrator(monkeypatch, "s", _RecordingStream(["Hel", "lo"]))
    orch.memory_manager = object()  # truthy: memory configured
    monkeypatch.setattr(
        orch,
        "_schedule_memory_write",
        lambda q, text, intent: writes.append((q, text, intent)),
    )

    async def no_recall(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(
        "core.orchestration.mixins._streaming.inject_memory_context", no_recall
    )

    [c async for c in orch.process_stream("greet")]

    assert writes == [("greet", "Hello", "s")]


class _FailingStream:
    async def handle(self, query: str, context: dict[str, Any]) -> AsyncIterator[str]:
        yield "partial"
        raise RuntimeError("provider dropped the stream")


@pytest.mark.asyncio
async def test_failed_stream_is_not_written_to_memory(monkeypatch):
    writes: list[Any] = []
    orch = _orchestrator(monkeypatch, "s", _FailingStream())
    orch.memory_manager = object()
    monkeypatch.setattr(orch, "_schedule_memory_write", lambda *a: writes.append(a))

    async def no_recall(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(
        "core.orchestration.mixins._streaming.inject_memory_context", no_recall
    )

    [c async for c in orch.process_stream("q")]

    assert writes == []
    assert get_active_budget() is None


class _LeakyStream:
    async def handle(self, query: str, context: dict[str, Any]) -> AsyncIterator[str]:
        raise RuntimeError("psycopg: connection to 10.0.4.7:5432 refused")
        yield ""  # pragma: no cover - makes this an async generator


@pytest.mark.asyncio
async def test_handler_failure_does_not_leak_the_exception_text(monkeypatch):
    # Regression: the client received "[ERROR] <exception text>", which can
    # carry internal hosts, SQL or paths.
    from core.orchestration.mixins.execution import STREAM_ERROR_MESSAGE

    orch = _orchestrator(monkeypatch, "s", _LeakyStream())
    chunks = [c async for c in orch.process_stream("q")]

    assert chunks == [STREAM_ERROR_MESSAGE]
    assert "10.0.4.7" not in "".join(chunks)
