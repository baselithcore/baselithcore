"""LazyServiceRegistry.shutdown_all: sync/async hooks and the stop() fallback."""

from __future__ import annotations

from typing import Any

import pytest

from core.di import lazy_registry as lazy_registry_module
from core.di.lazy_registry import LazyServiceRegistry
from core.events import EventNames
from core.events.bus import EventBus


@pytest.fixture
def errors(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture ``logger.error`` calls from the registry module."""
    seen: list[str] = []
    monkeypatch.setattr(
        lazy_registry_module.logger, "error", lambda msg, *a, **k: seen.append(msg)
    )
    return seen


async def _registry_with(instance: Any) -> LazyServiceRegistry:
    registry = LazyServiceRegistry()

    async def factory() -> Any:
        return instance

    registry.register_factory("svc", factory)
    await registry.get_or_create("svc")
    return registry


class _SyncShutdown:
    def __init__(self) -> None:
        self.calls = 0

    def shutdown(self) -> None:
        self.calls += 1


class _StopOnly:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _AsyncStopOnly:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class _ShutdownAndStop:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def shutdown(self) -> None:
        self.calls.append("shutdown")

    def stop(self) -> None:
        self.calls.append("stop")


@pytest.mark.asyncio
async def test_sync_shutdown_is_called_without_error(errors: list[str]) -> None:
    svc = _SyncShutdown()
    registry = await _registry_with(svc)
    await registry.shutdown_all()
    assert svc.calls == 1
    assert errors == []


@pytest.mark.asyncio
async def test_real_postgres_storage_sync_shutdown_logs_no_error(
    errors: list[str],
) -> None:
    from core.storage.postgres import PostgresStorage

    storage = PostgresStorage.__new__(PostgresStorage)
    registry = await _registry_with(storage)
    await registry.shutdown_all()
    assert errors == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [_StopOnly, _AsyncStopOnly])
async def test_stop_is_the_fallback_hook(cls: type) -> None:
    svc = cls()
    registry = await _registry_with(svc)
    await registry.shutdown_all()
    assert svc.stopped is True
    assert not registry.is_initialized("svc")


@pytest.mark.asyncio
async def test_only_the_preferred_hook_runs() -> None:
    svc = _ShutdownAndStop()
    registry = await _registry_with(svc)
    await registry.shutdown_all()
    assert svc.calls == ["shutdown"]


@pytest.mark.asyncio
async def test_evolution_service_is_unsubscribed_on_shutdown() -> None:
    from core.learning.evolution import EvolutionService

    service = EvolutionService(enable_auto_finetuning=False)
    bus = EventBus()
    service.event_bus = bus
    service.start()
    assert bus._handlers[EventNames.EVALUATION_COMPLETED]

    registry = await _registry_with(service)
    await registry.shutdown_all()

    assert not bus._handlers[EventNames.EVALUATION_COMPLETED]
    assert service.get_evolution_stats()["running"] is False


def test_auto_finetuning_stop_unsubscribes() -> None:
    from core.learning.auto_finetuning import AutoFineTuningService

    service = AutoFineTuningService()
    bus = EventBus()
    service.event_bus = bus
    service.start()
    assert bus._handlers[EventNames.EVALUATION_COMPLETED]

    service.stop()
    assert not bus._handlers[EventNames.EVALUATION_COMPLETED]
    # Idempotent: a second stop() must not raise.
    service.stop()
