"""A streamed run is a checkpointed run: crash, resume, replay.

The stream handler drives a typed agent on the run's checkpoint (the shape of
a plugin's streaming agent). A first pass dies after the agent's first model
turn and tool call; reopening the run must replay both — the tool and the
model are counted — and continue; reopening a completed run must re-emit the
recorded chunks without running anything.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from baselith.core.agent import Agent
from baselith.core.agent.events import TextDelta
from baselith.core.orchestration.checkpoint import (
    STATUS_COMPLETED,
    STATUS_FAILED,
)
from baselith.core.orchestration.checkpoint_errors import CheckpointLeaseHeldError
from baselith.core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from baselith.core.orchestration.idempotency import InMemoryToolLedger
from baselith.core.orchestration.mixins._streaming import STREAM_CHUNKS_KEY
from baselith.core.orchestration.orchestrator import Orchestrator
from baselith.core.orchestration.stream_runs import (
    MODE_FRESH,
    MODE_REPLAYED,
    MODE_RESUMED,
    open_stream_run,
)
from baselith.core.reasoning.react import ToolDefinition
from baselith.core.services.llm.messages import Message, TextBlock, ToolUseBlock
from baselith.core.services.llm.tool_calling import LLMResult, ToolCall

pytestmark = pytest.mark.unit


class _Death(BaseException):
    """The process dies mid-stream."""


class _Model:
    def __init__(self, turns: list[Any]) -> None:
        self.supports_messages = True
        self.supports_text_streaming = True
        self.calls = 0
        self._turns = list(turns)

    async def generate_messages(self, messages: list[Message], **kwargs: Any) -> Any:
        self.calls += 1
        turn = self._turns.pop(0)
        if turn is _Death:
            raise _Death("killed")
        on_text = kwargs.get("on_text")
        if on_text is not None and turn.text:
            for word in turn.text.split(" "):
                on_text(word + " ")
        return turn


def _lookup_turn() -> LLMResult:
    call = ToolCall(id="toolu-1", name="lookup", arguments={"q": "rome"})
    return LLMResult(
        tool_calls=[call],
        message=Message(
            role="assistant",
            content=[ToolUseBlock(id=call.id, name=call.name, input=call.arguments)],
        ),
    )


def _answer(text: str) -> LLMResult:
    return LLMResult(
        text=text, message=Message(role="assistant", content=[TextBlock(text=text)])
    )


class _AgentStream:
    """A plugin stream handler: a typed agent on the run's checkpoint."""

    def __init__(self) -> None:
        self.model: _Model = _Model([])
        self.lookups = 0

    def _agent(self) -> Agent[Any]:
        def lookup(q: str) -> str:
            self.lookups += 1
            return f"{q}: 2.8M people"

        return Agent(
            llm_service=self.model,
            tools=[ToolDefinition(name="lookup", fn=lookup, description="d")],
            tool_ledger=InMemoryToolLedger(),
            loop_limits=None,
        )

    async def handle(self, query: str, context: dict[str, Any]) -> Any:
        async for event in self._agent().run_events(
            query, checkpoint=context["checkpoint"]
        ):
            if isinstance(event, TextDelta):
                yield event.text


def _orchestrator(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryCheckpointStore, handler: Any
) -> Orchestrator:
    orch = Orchestrator(checkpoint_store=store)
    orch._stream_handlers["s"] = handler

    async def classify(query: str) -> str:
        return "s"

    monkeypatch.setattr(orch, "classify_intent_async", classify)
    return orch


async def _drain(source: Any) -> list[str]:
    return [chunk async for chunk in source]


async def test_a_crashed_stream_resumes_then_replays(monkeypatch) -> None:
    store = InMemoryCheckpointStore()
    handler = _AgentStream()
    orch = _orchestrator(monkeypatch, store, handler)

    # Pass 1: the model's first turn asks for lookup, the tool runs, then the
    # process dies during the second model call.
    handler.model = _Model([_lookup_turn(), _Death])
    opened = await open_stream_run(orch, "population of rome?", {}, "stream-1")
    assert opened.mode == MODE_FRESH
    with pytest.raises(_Death):
        await _drain(opened.source)
    assert (handler.model.calls, handler.lookups) == (2, 1)
    crashed = await store.load("stream-1")
    assert crashed is not None and crashed.status != STATUS_COMPLETED

    # Pass 2 (the client retried with the same key): recorded turn and tool
    # call replay; only the interrupted turn reaches the model.
    handler.model = _Model([_answer("Rome has 2.8M people")])
    opened = await open_stream_run(orch, "population of rome?", {}, "stream-1")
    assert opened.mode == MODE_RESUMED
    chunks = await _drain(opened.source)
    assert "".join(chunks) == "Rome has 2.8M people "
    assert (handler.model.calls, handler.lookups) == (1, 1)

    done = await store.load("stream-1")
    assert done is not None and done.status == STATUS_COMPLETED
    assert done.answer == "".join(chunks)
    assert done.plugin_data[STREAM_CHUNKS_KEY] == [len(c) for c in chunks]
    assert done.owner is None or done.lease_expires_at is None  # lease released

    # Pass 3 (the response was lost): the same events, nothing executed.
    handler.model = _Model([])
    opened = await open_stream_run(orch, "population of rome?", {}, "stream-1")
    assert opened.mode == MODE_REPLAYED
    assert await _drain(opened.source) == chunks
    assert (handler.model.calls, handler.lookups) == (0, 1)


async def test_a_held_lease_refuses_before_streaming(monkeypatch) -> None:
    store = InMemoryCheckpointStore()
    handler = _AgentStream()
    orch = _orchestrator(monkeypatch, store, handler)
    handler.model = _Model([_lookup_turn(), _Death])
    with pytest.raises(_Death):
        await _drain((await open_stream_run(orch, "q", {}, "stream-2")).source)

    held = await store.load("stream-2")
    assert held is not None
    held.owner = "another-replica"
    held.lease_expires_at = time.time() + 60
    await store.save(held)
    with pytest.raises(CheckpointLeaseHeldError):
        await open_stream_run(orch, "q", {}, "stream-2")


async def test_a_disconnect_leaves_the_run_resumable(monkeypatch) -> None:
    store = InMemoryCheckpointStore()
    handler = _AgentStream()
    orch = _orchestrator(monkeypatch, store, handler)
    answer = " ".join(f"word{i}" for i in range(200))
    handler.model = _Model([_answer(answer)])

    source = orch.process_stream("q", {}, run_id="stream-3")
    first = await source.__anext__()
    assert answer.startswith(first.strip()) and len(first) < len(answer)
    await source.aclose()  # the client went away mid-answer

    stored = await store.load("stream-3")
    assert stored is not None
    assert (stored.status, stored.error) == (STATUS_FAILED, "cancelled")
    assert stored.lease_expires_at is None  # released: a retry may claim it

    # The model turn had finished (the guard was holding text back): the
    # retry replays it instead of paying for the answer again.
    handler.model = _Model([])
    opened = await open_stream_run(orch, "q", {}, "stream-3")
    assert opened.mode == MODE_RESUMED
    resumed = "".join(await _drain(opened.source))
    assert resumed.strip() == answer
    assert handler.model.calls == 0


async def test_a_streamed_run_gets_a_checkpoint_without_an_id(monkeypatch) -> None:
    store = InMemoryCheckpointStore()
    handler = _AgentStream()
    orch = _orchestrator(monkeypatch, store, handler)
    handler.model = _Model([_answer("hi there")])
    context: dict[str, Any] = {}
    chunks = await _drain(orch.process_stream("q", context))
    run_id = context["checkpoint"].run_id
    stored = await store.load(run_id)
    assert stored is not None and stored.answer == "".join(chunks)


async def test_a_reused_id_with_another_query_runs_fresh(monkeypatch) -> None:
    store = InMemoryCheckpointStore()
    handler = _AgentStream()
    orch = _orchestrator(monkeypatch, store, handler)
    handler.model = _Model([_answer("a"), _answer("b")])
    await _drain((await open_stream_run(orch, "first", {}, "stream-4")).source)
    opened = await open_stream_run(orch, "second", {}, "stream-4")
    assert (opened.mode, opened.run_id) == (MODE_FRESH, None)
    assert await _drain(opened.source) == ["b "]


async def test_a_non_stream_completion_replays_through_the_output_guard(
    monkeypatch,
) -> None:
    """The recovery sweep completes an interrupted stream via process()."""
    from baselith.core.orchestration.checkpoint import Checkpoint

    store = InMemoryCheckpointStore()
    orch = _orchestrator(monkeypatch, store, _AgentStream())
    await store.save(
        Checkpoint(
            run_id="stream-5", query="q", status=STATUS_COMPLETED, answer="swept"
        )
    )
    opened = await open_stream_run(orch, "q", {}, "stream-5")
    assert opened.mode == MODE_REPLAYED
    assert await _drain(opened.source) == ["swept"]


async def test_a_drained_stream_is_yielded_for_another_replica(monkeypatch) -> None:
    from baselith.core.orchestration.checkpoint_lease import YIELDED_KEY

    monkeypatch.setattr("baselith.core.lifecycle.drain.is_draining", lambda: True)
    store = InMemoryCheckpointStore()
    handler = _AgentStream()
    orch = _orchestrator(monkeypatch, store, handler)
    handler.model = _Model([_answer(" ".join(f"w{i}" for i in range(200)))])

    source = orch.process_stream("q", {}, run_id="stream-6")
    await source.__anext__()
    await source.aclose()

    stored = await store.load("stream-6")
    assert stored is not None
    assert stored.status == "running" and YIELDED_KEY in stored.plugin_data
    assert stored.lease_expires_at is None
