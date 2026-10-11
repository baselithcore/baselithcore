"""``FLOW_COMPLETED`` carries what online evaluation needs.

The success event used to be emitted *before* the output guard, so it carried
text the user never saw and none of the guard's verdicts, and it had no tenant
— subscribers handle it outside the request, where the tenant context is gone.
The streaming path emitted nothing at all, so streamed traffic was invisible
to every ``FLOW_COMPLETED`` subscriber.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from baselith.core.context import reset_tenant_context, set_tenant_context
from baselith.core.events import EventNames
from baselith.core.orchestration.mixins._flow_events import FLOW_EVENT_KEY
from baselith.core.orchestration.orchestrator import Orchestrator


@pytest.fixture
def bus(monkeypatch) -> MagicMock:
    mock = MagicMock()
    monkeypatch.setattr(
        "baselith.core.orchestration.mixins._flow_events.get_event_bus", lambda: mock
    )
    monkeypatch.setattr(
        "baselith.core.orchestration.mixins.execution.get_event_bus", lambda: mock
    )
    return mock


def _completed(bus: MagicMock) -> list[dict[str, Any]]:
    return [
        call.args[1]
        for call in bus.emit_sync.call_args_list
        if call.args[0] == EventNames.FLOW_COMPLETED
    ]


def _orchestrator(intent: str) -> Orchestrator:
    classifier = MagicMock()
    classifier.classify = AsyncMock(return_value=intent)
    return Orchestrator(intent_classifier=classifier)


async def test_event_carries_guarded_response_tenant_and_spend(bus, monkeypatch):
    monkeypatch.setenv("BASELITH_ORCHESTRATOR_GUARDRAILS", "true")
    orch = _orchestrator("qa")
    handler = AsyncMock()
    handler.handle.return_value = {"response": "Write to jane.doe@example.com today."}
    orch.register_handler("qa", handler)

    token = set_tenant_context("acme")
    try:
        context: dict[str, Any] = {}
        result = await orch.process("contact?", context, run_id="run-42")
    finally:
        reset_tenant_context(token)

    (data,) = _completed(bus)
    assert data["response"] == result["response"]  # what the user received
    assert data["tenant_id"] == "acme"
    assert data["run_id"] == "run-42"
    assert data["success"] is True
    assert isinstance(data["cost_usd"], float) and isinstance(data["tokens"], int)
    assert data["guardrails"] == result["guardrails"]  # the guard redacted PII
    assert "jane.doe@example.com" not in data["response"]
    assert FLOW_EVENT_KEY not in context  # the staged payload never lingers


async def test_guard_failure_discards_the_staged_event(bus, monkeypatch):
    orch = _orchestrator("qa")
    handler = AsyncMock()
    handler.handle.return_value = {"response": "fine"}
    orch.register_handler("qa", handler)

    async def boom(result: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("guard down")

    monkeypatch.setattr(
        "baselith.core.orchestration.guard_pipeline.guard_output_async", boom
    )
    context: dict[str, Any] = {}
    with pytest.raises(RuntimeError):
        await orch.process("q", context)
    assert _completed(bus) == []
    assert FLOW_EVENT_KEY not in context


class _Stream:
    async def handle(self, query: str, context: dict[str, Any]) -> AsyncIterator[str]:
        for chunk in ("streamed ", "answer"):
            yield chunk


async def test_streamed_flow_emits_flow_completed(bus):
    orch = _orchestrator("s")
    orch._stream_handlers["s"] = _Stream()

    chunks = [c async for c in orch.process_stream("q", {"run_id": "run-7"})]

    (data,) = _completed(bus)
    assert data["response"] == "".join(chunks)
    assert data["streamed"] is True
    assert data["run_id"] == "run-7"
    assert data["intent"] == "s" and data["success"] is True
    assert data["tenant_id"] == "default"


async def test_abandoned_stream_emits_nothing(bus):
    orch = _orchestrator("s")
    orch._stream_handlers["s"] = _Stream()

    stream = orch.process_stream("q", {})
    await stream.__anext__()
    await stream.aclose()

    assert _completed(bus) == []
