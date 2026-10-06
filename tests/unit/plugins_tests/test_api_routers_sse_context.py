"""The SSE heartbeat must not split a stream's reads across contexts.

Each read used to run as its own task, i.e. in a fresh copy of the caller's
context: a context variable the stream set on its first step (the request
budget, the plugin attribution) was gone on the second, and resetting its
token in the stream's ``finally`` raised ``ValueError``.
"""

from __future__ import annotations

import contextvars
from collections.abc import AsyncIterator

from plugins.api_routers._sse import HEARTBEAT, HeartbeatSource

_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "sse_test_var", default=None
)


async def test_context_set_by_the_source_survives_across_reads() -> None:
    seen: list[str | None] = []
    closed: list[bool] = []

    async def source() -> AsyncIterator[str]:
        token = _var.set("bound")
        try:
            yield "first"
            seen.append(_var.get())
            yield "second"
            seen.append(_var.get())
        finally:
            _var.reset(token)  # raises if run in another context
            closed.append(True)

    hb = HeartbeatSource(source(), interval=5.0)
    assert await hb.next() == "first"
    assert await hb.next() == "second"
    await hb.aclose()

    assert seen == ["bound"]
    assert closed == [True]
    assert _var.get() is None  # the caller's context was never touched


async def test_quiet_interval_yields_heartbeat_and_keeps_the_read() -> None:
    import asyncio

    release = asyncio.Event()

    async def source() -> AsyncIterator[str]:
        await release.wait()
        yield "late"

    hb = HeartbeatSource(source(), interval=0.01)
    assert await hb.next() is HEARTBEAT
    release.set()
    assert await hb.next() == "late"
    await hb.aclose()
