"""Online evaluation wiring: boot start through the lazy registry, shutdown stop."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import baselith.core.config.evaluation as eval_cfg
from baselith.core.api import _runtime_services as runtime
from baselith.core.config.evaluation import EvaluationConfig
from baselith.core.di.lazy_registry import LazyServiceRegistry
from baselith.core.evaluation.service import EvaluationService
from baselith.core.events import EventBus


@pytest.fixture
def registry(monkeypatch) -> LazyServiceRegistry:
    reg = LazyServiceRegistry()
    monkeypatch.setattr("baselith.core.di.lazy_registry.get_lazy_registry", lambda: reg)
    return reg


def _register(reg: LazyServiceRegistry, service: EvaluationService) -> None:
    async def factory() -> EvaluationService:
        service.start()
        return service

    reg.register_factory("evaluation", factory)


@pytest.mark.parametrize(
    ("enabled", "online", "started"),
    [(True, True, True), (True, False, False), (False, True, False)],
)
async def test_boot_start_needs_both_switches(
    monkeypatch, registry, enabled: bool, online: bool, started: bool
) -> None:
    config = EvaluationConfig(enabled=enabled, online_enabled=online)
    monkeypatch.setattr(eval_cfg, "_evaluation_config", config)
    service = EvaluationService(event_bus=EventBus(), config=config)
    _register(registry, service)
    app = SimpleNamespace(state=SimpleNamespace())

    await runtime._start_online_evaluation(app)

    assert (getattr(app.state, "evaluation_service", None) is service) is started
    assert service.running is started
    if started:
        tasks = service._background_tasks()
        await runtime.stop_runtime_services(app)
        assert service.running is False
        assert all(task.done() for task in tasks)


async def test_boot_start_reuses_a_plugin_started_service(
    monkeypatch, registry
) -> None:
    monkeypatch.setattr(
        eval_cfg,
        "_evaluation_config",
        EvaluationConfig(enabled=True, online_enabled=True),
    )
    existing = object()
    app = SimpleNamespace(state=SimpleNamespace(evaluation_service=existing))
    await runtime._start_online_evaluation(app)
    assert app.state.evaluation_service is existing


async def test_boot_start_failure_degrades(monkeypatch, registry) -> None:
    monkeypatch.setattr(
        eval_cfg,
        "_evaluation_config",
        EvaluationConfig(enabled=True, online_enabled=True),
    )
    app = SimpleNamespace(state=SimpleNamespace())
    await runtime._start_online_evaluation(app)  # no factory registered: logged
    assert getattr(app.state, "evaluation_service", None) is None
