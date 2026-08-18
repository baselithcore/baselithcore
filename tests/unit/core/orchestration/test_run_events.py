"""Tests for structured run-event streaming (astream_events equivalent)."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from core.api.events import AgentEvent, EventType
from core.orchestration.checkpoint import (
    Checkpoint,
    CheckpointManager,
    InMemoryCheckpointStore,
)
from core.orchestration.run_events import (
    TERMINAL_EVENT_TYPES,
    RunEventStream,
    get_run_event_stream,
    reset_run_event_stream,
    stream_run_events,
)

pytestmark = [pytest.mark.contract]


@pytest.fixture(autouse=True)
def _fresh_stream():
    reset_run_event_stream()
    yield
    reset_run_event_stream()


def _event(event_type=EventType.THOUGHT, content="x"):
    return AgentEvent(type=event_type, content=content)


# --------------------------------------------------------------------------- #
# RunEventStream primitives
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestRunEventStream:
    async def test_publish_without_subscribers_is_noop(self):
        stream = RunEventStream()
        assert stream.publish("r1", _event()) == 0

    async def test_publish_subscribe_roundtrip(self):
        stream = RunEventStream()
        async with stream.subscribe("r1") as sub:
            assert stream.publish("r1", _event(content="hello")) == 1
            event = await asyncio.wait_for(anext(aiter(sub)), timeout=1)
            assert event.content == "hello"

    async def test_two_subscribers_both_receive(self):
        stream = RunEventStream()
        async with stream.subscribe("r1") as sub_a, stream.subscribe("r1") as sub_b:
            assert stream.publish("r1", _event(content="e")) == 2
            got_a = await asyncio.wait_for(anext(aiter(sub_a)), timeout=1)
            got_b = await asyncio.wait_for(anext(aiter(sub_b)), timeout=1)
            assert got_a.content == got_b.content == "e"

    async def test_overflow_drops_oldest(self):
        stream = RunEventStream()
        async with stream.subscribe("r1", max_queue=2) as sub:
            for i in range(4):
                stream.publish("r1", _event(content=str(i)))
            first = await asyncio.wait_for(anext(aiter(sub)), timeout=1)
            second = await asyncio.wait_for(anext(aiter(sub)), timeout=1)
            assert [first.content, second.content] == ["2", "3"]

    async def test_close_unregisters(self):
        stream = RunEventStream()
        sub = stream.subscribe("r1")
        async with sub:
            pass
        assert stream.publish("r1", _event()) == 0

    async def test_singleton_reset(self):
        a = get_run_event_stream()
        assert get_run_event_stream() is a
        reset_run_event_stream()
        assert get_run_event_stream() is not a

    async def test_terminal_types(self):
        assert EventType.RESPONSE_FINAL in TERMINAL_EVENT_TYPES
        assert EventType.ERROR in TERMINAL_EVENT_TYPES
        assert EventType.HUMAN_REQUEST in TERMINAL_EVENT_TYPES
        assert EventType.TOOL_CALL not in TERMINAL_EVENT_TYPES


# --------------------------------------------------------------------------- #
# Producers: CheckpointManager.run_step
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestToolStepEvents:
    async def test_run_step_emits_call_and_result(self):
        stream = get_run_event_stream()
        store = InMemoryCheckpointStore()
        mgr = CheckpointManager(store, Checkpoint(run_id="r1"))
        async with stream.subscribe("r1") as sub:

            async def tool():
                return "out"

            await mgr.run_step("toolA", {"x": 1}, tool)
            call = await asyncio.wait_for(anext(aiter(sub)), timeout=1)
            result = await asyncio.wait_for(anext(aiter(sub)), timeout=1)
        assert call.type == EventType.TOOL_CALL
        assert call.data["tool_name"] == "toolA"
        assert "args" not in call.data  # payload safety: no args in events
        assert result.type == EventType.TOOL_RESULT
        assert result.data["replayed"] is False
        assert "result" not in result.data

    async def test_replayed_step_flagged(self):
        stream = get_run_event_stream()
        store = InMemoryCheckpointStore()
        cp = Checkpoint(run_id="r1")
        mgr = CheckpointManager(store, cp)

        async def tool():
            return "out"

        await mgr.run_step("toolA", {"x": 1}, tool)

        resumed = await store.load("r1")
        mgr2 = CheckpointManager(store, resumed)
        async with stream.subscribe("r1") as sub:
            await mgr2.run_step("toolA", {"x": 1}, tool)
            call = await asyncio.wait_for(anext(aiter(sub)), timeout=1)
            result = await asyncio.wait_for(anext(aiter(sub)), timeout=1)
        assert call.type == EventType.TOOL_CALL
        assert result.data["replayed"] is True


# --------------------------------------------------------------------------- #
# Producers: orchestrator lifecycle + stream_run_events helper
# --------------------------------------------------------------------------- #


class _OkHandler:
    async def handle(self, query, context):
        mgr = context.get("checkpoint")
        if mgr is not None:

            async def step():
                return "A"

            await mgr.run_step("toolA", {"q": query}, step)
        return {"response": "done"}


class _BoomHandler:
    async def handle(self, query, context):
        raise RuntimeError("boom")


def _orchestrator(store=None, handler=None):
    from core.orchestration.orchestrator import Orchestrator

    orch = Orchestrator(checkpoint_store=store, default_intent="test_intent")
    orch.classify_intent_async = AsyncMock(return_value="test_intent")  # type: ignore
    orch._flow_handlers["test_intent"] = handler or _OkHandler()
    return orch


@pytest.mark.asyncio
class TestLifecycleEvents:
    async def test_full_event_sequence_with_store(self):
        orch = _orchestrator(store=InMemoryCheckpointStore())
        events = []
        async for event in stream_run_events(orch, "hello", run_id="run-1"):
            events.append(event)
        types = [e.type for e in events]
        assert types == [
            EventType.RUN_STARTED,
            EventType.TOOL_CALL,
            EventType.TOOL_RESULT,
            EventType.RESPONSE_FINAL,
        ]
        assert events[-1].data["response"] == "done"
        assert all(e.data["run_id"] == "run-1" for e in events)

    async def test_lifecycle_without_checkpoint_store(self):
        orch = _orchestrator(store=None)
        events = [e async for e in stream_run_events(orch, "hello")]
        types = [e.type for e in events]
        assert types == [EventType.RUN_STARTED, EventType.RESPONSE_FINAL]

    async def test_error_event_on_handler_failure(self):
        orch = _orchestrator(store=InMemoryCheckpointStore(), handler=_BoomHandler())
        events = [e async for e in stream_run_events(orch, "hello", run_id="r-err")]
        assert events[-1].type == EventType.ERROR
        assert "boom" in events[-1].data["error"]

    async def test_no_run_id_no_store_publishes_nothing_ambient(self):
        """Direct process() without run_id/store must not touch the stream."""
        stream = get_run_event_stream()
        published = []
        original = stream.publish
        stream.publish = lambda rid, ev: published.append(rid) or original(rid, ev)
        orch = _orchestrator(store=None)
        await orch.process("hello", intent="test_intent")
        assert published == []
