"""Async usage sinks are strongly referenced and drained at shutdown.

``asyncio.ensure_future`` alone leaves the task referenced only by the loop's
weak set: it can be collected mid-flight, and at shutdown the ledger write is
dropped with the loop. The module keeps every pending task and
:func:`drain_usage_sinks` awaits them.
"""

from __future__ import annotations

import asyncio

from core.services.llm.usage import Usage
from core.services.llm.usage_sinks import (
    UsageReport,
    _pending_sink_tasks,
    drain_usage_sinks,
    emit_usage_report,
    register_usage_sink,
    unregister_usage_sink,
)


def _report() -> UsageReport:
    return UsageReport(model="m", usage=Usage(input_tokens=3, output_tokens=2))


async def test_pending_task_is_held_and_drained() -> None:
    done: list[str] = []
    release = asyncio.Event()

    async def sink(_report: UsageReport) -> None:
        await release.wait()
        done.append("written")

    register_usage_sink(sink)
    try:
        emit_usage_report(_report())
        assert len(_pending_sink_tasks) == 1
        release.set()
        assert await drain_usage_sinks(timeout=1.0) == 0
        assert done == ["written"]
        assert not _pending_sink_tasks
    finally:
        unregister_usage_sink(sink)


async def test_drain_cancels_what_outlives_the_timeout() -> None:
    async def sink(_report: UsageReport) -> None:
        await asyncio.sleep(30)

    register_usage_sink(sink)
    try:
        emit_usage_report(_report())
        assert await drain_usage_sinks(timeout=0.05) == 1
        await asyncio.sleep(0)
        assert not _pending_sink_tasks
    finally:
        unregister_usage_sink(sink)


async def test_drain_with_nothing_pending_returns_at_once() -> None:
    assert await drain_usage_sinks(timeout=0.01) == 0
