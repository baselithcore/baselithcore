"""Lifecycle event topic/state matrix (core.plugins.lifecycle_events)."""

from __future__ import annotations

from typing import Any

import pytest

from core.plugins import lifecycle_events as mod


class _FakeBus:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, name: str, data: dict[str, Any], **_: Any) -> int:
        self.calls.append((name, data))
        return 1


@pytest.fixture
def bus(monkeypatch: pytest.MonkeyPatch) -> _FakeBus:
    fake = _FakeBus()
    monkeypatch.setattr("core.events.bus.get_event_bus", lambda: fake)
    return fake


@pytest.mark.parametrize(
    "op,ok,topic,state",
    [
        ("enable", True, mod.PLUGIN_ACTIVATED, "active"),
        ("enable", False, mod.PLUGIN_FAILED, "failed"),
        ("disable", True, mod.PLUGIN_DEACTIVATED, "disabled"),
        ("reload", True, mod.PLUGIN_RELOADED, "active"),
        ("reload", False, mod.PLUGIN_FAILED, "failed"),
    ],
)
async def test_emits_expected_topic(
    bus: _FakeBus, op: str, ok: bool, topic: str, state: str
) -> None:
    await mod.emit_lifecycle_event(op, "demo", ok)
    assert bus.calls == [
        (topic, {"plugin": "demo", "state": state, "op": op, "ok": ok})
    ]


async def test_failed_disable_emits_nothing(bus: _FakeBus) -> None:
    # State didn't change on a failed disable — nothing to announce, and
    # emitting plugin.failed here would wrongly flag a still-active plugin.
    await mod.emit_lifecycle_event("disable", "demo", False)
    assert bus.calls == []


async def test_unknown_op_emits_nothing(bus: _FakeBus) -> None:
    await mod.emit_lifecycle_event("frobnicate", "demo", True)
    assert bus.calls == []


async def test_emit_failure_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("bus down")

    monkeypatch.setattr("core.events.bus.get_event_bus", _boom)
    await mod.emit_lifecycle_event("enable", "demo", True)  # must not raise
