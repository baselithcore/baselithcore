"""Tests for the default EngineeredLoop escalation sink."""

from __future__ import annotations

import pytest
from core.loops.escalation import build_default_escalation

from core.loops.engineered import LoopOutcome

pytestmark = [pytest.mark.unit]


def _outcome() -> LoopOutcome:
    return LoopOutcome(
        status="exhausted", goal="tests green", attempts=3, reason="max attempts"
    )


class _Human:
    def __init__(self, fail: bool = False) -> None:
        self.notified: list[tuple[str, dict]] = []
        self._fail = fail

    async def notify(self, message: str, context: dict | None = None) -> None:
        if self._fail:
            raise RuntimeError("channel down")
        self.notified.append((message, context or {}))


class _Webhooks:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []

    async def emit(self, event_type: str, data: dict, *, tenant_id: str = "default"):
        self.emitted.append((event_type, data))
        return []


class TestBuildDefaultEscalation:
    async def test_notifies_human_and_emits_webhook(self):
        human, webhooks = _Human(), _Webhooks()
        hook = build_default_escalation(human=human, webhooks=webhooks)
        await hook(_outcome())

        assert len(human.notified) == 1
        message, context = human.notified[0]
        assert "exhausted" in message
        assert context["goal"] == "tests green"

        assert webhooks.emitted == [("loop.escalated", _outcome().to_state())]

    async def test_sink_failure_does_not_block_other_sinks(self):
        human, webhooks = _Human(fail=True), _Webhooks()
        hook = build_default_escalation(human=human, webhooks=webhooks)
        await hook(_outcome())
        assert len(webhooks.emitted) == 1

    async def test_no_sinks_is_noop(self):
        hook = build_default_escalation()
        await hook(_outcome())
