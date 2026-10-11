"""``ChatService.handle_chat_stream_async(run_id=...)`` opens a named run.

The orchestrator is real (with an in-memory checkpoint store) so the attempt
kinds come from the store, not from a stub.
"""

from __future__ import annotations

from typing import Any

import pytest

from baselith.core.models.chat import ChatRequest
from baselith.core.orchestration.checkpoint import STATUS_AWAITING_APPROVAL, Checkpoint
from baselith.core.orchestration.checkpoint_errors import CheckpointLeaseHeldError
from baselith.core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from baselith.core.orchestration.orchestrator import Orchestrator
from baselith.core.services.chat.service import ChatService, ChatServiceConfig
from baselith.core.services.chat.stream_session import ChatStream

pytestmark = pytest.mark.unit


class _Words:
    def __init__(self) -> None:
        self.calls = 0
        self.words: tuple[str, ...] = ("the ", "answer")

    async def handle(self, query: str, context: dict[str, Any]) -> Any:
        self.calls += 1
        for word in self.words:
            yield word


def _service(monkeypatch: pytest.MonkeyPatch) -> tuple[ChatService, _Words, Any]:
    store = InMemoryCheckpointStore()
    orch = Orchestrator(checkpoint_store=store)
    handler = _Words()
    orch._stream_handlers["s"] = handler

    async def classify(query: str) -> str:
        return "s"

    monkeypatch.setattr(orch, "classify_intent_async", classify)
    service = ChatService(config=ChatServiceConfig(history_enabled=False))
    service._agent = orch
    return service, handler, store


async def _text(stream: Any) -> str:
    return "".join([chunk async for chunk in stream])


async def test_a_named_stream_replays_once_completed(monkeypatch) -> None:
    service, handler, _ = _service(monkeypatch)
    req = ChatRequest(query="q")

    first = await service.handle_chat_stream_async(req, run_id="chat-1")
    assert isinstance(first, ChatStream) and first.mode == "fresh"
    assert await _text(first) == "the answer"

    again = await service.handle_chat_stream_async(req, run_id="chat-1")
    assert isinstance(again, ChatStream) and again.mode == "replayed"
    assert await _text(again) == "the answer"
    assert handler.calls == 1


async def test_a_paused_run_streams_its_notice(monkeypatch) -> None:
    service, handler, store = _service(monkeypatch)
    await store.save(
        Checkpoint(run_id="chat-2", query="q", status=STATUS_AWAITING_APPROVAL)
    )
    stream = await service.handle_chat_stream_async(
        ChatRequest(query="q"), run_id="chat-2"
    )
    assert getattr(stream, "mode", None) == "paused"
    assert "awaiting human approval" in await _text(stream)
    assert handler.calls == 0


async def test_a_held_lease_propagates_for_a_409(monkeypatch) -> None:
    import time

    service, _, store = _service(monkeypatch)
    await store.save(
        Checkpoint(
            run_id="chat-3",
            query="q",
            owner="elsewhere",
            lease_expires_at=time.time() + 60,
        )
    )
    with pytest.raises(CheckpointLeaseHeldError):
        await service.handle_chat_stream_async(ChatRequest(query="q"), run_id="chat-3")


async def test_closing_the_chat_stream_records_the_interruption_at_once(
    monkeypatch,
) -> None:
    """The close reaches the run through every wrapper, not via the GC."""
    service, handler, store = _service(monkeypatch)
    handler.words = tuple(f"word{i} " for i in range(200))
    stream = await service.handle_chat_stream_async(
        ChatRequest(query="q"), run_id="chat-4"
    )
    first = await stream.__anext__()
    assert first and "".join(handler.words).startswith(first)
    await stream.aclose()  # the SSE layer's close on a client disconnect

    stored = await store.load("chat-4")
    assert stored is not None
    assert (stored.status, stored.error) == ("failed", "cancelled")
    assert stored.lease_expires_at is None
