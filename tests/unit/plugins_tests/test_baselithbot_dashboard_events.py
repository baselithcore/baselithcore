"""Baselithbot dashboard SSE stream: keepalive frames and live delivery."""

from __future__ import annotations

import asyncio

import pytest

from plugins.baselithbot.dashboard import bus as bus_module
from plugins.baselithbot.dashboard.bus import DashboardEventBus
from plugins.baselithbot.dashboard.routes import events as events_module


@pytest.fixture
def fresh_bus(monkeypatch: pytest.MonkeyPatch) -> DashboardEventBus:
    bus = DashboardEventBus()
    monkeypatch.setattr(events_module, "_BUS", bus)
    monkeypatch.setattr(bus_module, "_BUS", bus)
    monkeypatch.setattr(events_module, "_KEEPALIVE_SECONDS", 0.01)
    return bus


async def test_idle_stream_emits_keepalive_and_stays_subscribed(
    fresh_bus: DashboardEventBus,
) -> None:
    stream = events_module._stream_frames()
    assert await anext(stream) == b": connected\n\n"

    # Nothing published: the stream must speak anyway, so proxies do not
    # cut it for idleness.
    assert await anext(stream) == events_module._KEEPALIVE_FRAME
    assert await anext(stream) == events_module._KEEPALIVE_FRAME

    # The keepalive timeouts must not have cancelled the subscription.
    assert len(fresh_bus._subscribers) == 1
    fresh_bus.publish("tick", {"n": 1})
    frame = b""
    while frame == events_module._KEEPALIVE_FRAME or not frame:
        frame = await anext(stream)
    assert frame.startswith(b"event: tick\ndata: ")
    assert b'"n": 1' in frame

    await stream.aclose()
    await asyncio.sleep(0)
    assert fresh_bus._subscribers == set()


async def test_close_during_idle_wait_unsubscribes(
    fresh_bus: DashboardEventBus,
) -> None:
    stream = events_module._stream_frames()
    await anext(stream)
    assert await anext(stream) == events_module._KEEPALIVE_FRAME
    await stream.aclose()
    for _ in range(3):
        await asyncio.sleep(0)
    assert fresh_bus._subscribers == set()


@pytest.fixture
def drain_state():
    from core.lifecycle import drain

    drain._reset_for_tests()
    yield drain
    drain._reset_for_tests()


async def test_idle_stream_ends_on_the_server_drain_signal(
    fresh_bus: DashboardEventBus, drain_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dashboard left open must not hold a rolling restart until the timeout."""
    monkeypatch.setattr(events_module, "_KEEPALIVE_SECONDS", 30.0)
    stream = events_module._stream_frames()
    await anext(stream)
    waiting = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0.01)
    assert not waiting.done()

    drain_state.mark_draining()

    # asyncio.wait, not wait_for: a timeout must not cancel the step, because
    # the generator turns a cancellation into an ordinary end of stream.
    done, _ = await asyncio.wait({waiting}, timeout=1.0)
    assert waiting in done, "the stream ignored the drain signal"
    with pytest.raises(StopAsyncIteration):
        waiting.result()
    for _ in range(3):
        await asyncio.sleep(0)
    assert fresh_bus._subscribers == set()


async def test_stream_opened_while_draining_ends_at_once(
    fresh_bus: DashboardEventBus, drain_state
) -> None:
    drain_state.mark_draining()
    stream = events_module._stream_frames()
    assert await anext(stream) == b": connected\n\n"
    step = asyncio.ensure_future(anext(stream))
    done, _ = await asyncio.wait({step}, timeout=1.0)
    assert step in done, "the stream ignored the drain signal"
    with pytest.raises(StopAsyncIteration):
        step.result()
    assert fresh_bus._subscribers == set()
