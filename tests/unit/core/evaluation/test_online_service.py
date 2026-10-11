"""Online EvaluationService: sampling, backpressure, judge, monitor, store, lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from baselith.core.config.evaluation import EvaluationConfig
from baselith.core.evaluation.online_checks import OnlineSample
from baselith.core.evaluation.online_store import InMemoryOnlineEvalStore
from baselith.core.evaluation.protocols import EvaluationResult, QualityLevel
from baselith.core.evaluation.service import EvaluationService
from baselith.core.events import EventBus, EventNames

GOOD = "X is a widely used configuration value that controls the cache TTL."


def _config(**kw: Any) -> EvaluationConfig:
    fields: dict[str, Any] = {
        "enabled": True,
        "online_sample_rate": 1.0,
        "online_retention_days": 0,
        # Per-process monitor: independent of CACHE_BACKEND in the environment
        # (the fleet monitor has its own tests in test_online_fleet.py).
        "online_fleet": False,
    }
    fields.update(kw)
    return EvaluationConfig(**fields)


def _flow(run_id: str = "run-1", **kw: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "intent": "qa",
        "query": "what is x?",
        "response": GOOD,
        "success": True,
        "run_id": run_id,
        "tenant_id": "acme",
        "duration_ms": 120,
        "cost_usd": 0.002,
        "tokens": 42,
    }
    data.update(kw)
    return data


def _judge(score: float = 0.9) -> MagicMock:
    evaluator = MagicMock()
    evaluator.evaluate = AsyncMock(
        return_value=EvaluationResult(
            score=score,
            quality=QualityLevel.EXCELLENT,
            feedback="ok",
            aspects={"relevance": score},
        )
    )
    return evaluator


class _Recorder:
    def __init__(self, bus: EventBus, *names: str) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        for name in names:
            bus.subscribe(name, self._handler(name))

    def _handler(self, name: str):
        async def handle(data: dict[str, Any]) -> None:
            self.events.append((name, data))

        return handle

    def named(self, name: str) -> list[dict[str, Any]]:
        return [data for event, data in self.events if event == name]


async def _drain(service: EvaluationService) -> None:
    assert service._queue is not None
    await asyncio.wait_for(service._queue.join(), timeout=5)


def test_disabled_config_does_not_subscribe() -> None:
    bus = MagicMock()
    service = EvaluationService(event_bus=bus, config=_config(enabled=False))
    service.start()
    bus.subscribe.assert_not_called()
    assert service.running is False


def test_settings_are_read_at_start_not_import(monkeypatch) -> None:
    import baselith.core.config.evaluation as cfg

    monkeypatch.setattr(cfg, "_evaluation_config", _config(online_sample_rate=0.25))
    bus = MagicMock()
    service = EvaluationService(event_bus=bus)
    service.start()  # outside a loop: workers deferred to the first event
    bus.subscribe.assert_called_once()
    assert service.summary()["sample_rate"] == 0.25
    service.stop()


async def test_sampled_flow_runs_checks_and_stores_without_raw_text() -> None:
    bus = EventBus()
    store = InMemoryOnlineEvalStore()
    service = EvaluationService(event_bus=bus, config=_config(), store=store)
    service.start()
    await service._on_flow_completed(_flow())
    await _drain(service)

    (record,) = await store.recent()
    assert record.source == "checks"
    assert record.score == 1.0
    assert record.run_id == "run-1" and record.tenant_id == "acme"
    assert record.signals["duration_ms"] == 120.0
    assert record.query_text is None and record.response_text is None
    summary = service.summary()
    assert summary["intents"][0]["intent"] == "qa"
    await service.shutdown()


async def test_raw_text_is_redacted_when_enabled() -> None:
    bus = EventBus()
    store = InMemoryOnlineEvalStore()
    service = EvaluationService(
        event_bus=bus, config=_config(online_store_raw_text=True), store=store
    )
    service.start()
    await service._on_flow_completed(
        _flow(
            query="reach me at jane.doe@example.com",
            response=GOOD
            + " token=abcd1234efgh5678 Bearer abcdefghijklmnop",  # gitleaks:allow (fake fixture)
        )
    )
    await _drain(service)
    (record,) = await store.recent()
    assert (
        record.query_text is not None
        and "jane.doe@example.com" not in record.query_text
    )
    assert record.response_text is not None
    assert "abcd1234efgh5678" not in record.response_text
    assert "abcdefghijklmnop" not in record.response_text
    await service.shutdown()


@pytest.mark.parametrize(
    "flow",
    [
        _flow(success=False),
        _flow(intent="evaluation_probe"),
        _flow(intent=None),
        {"intent": "qa", "success": False, "error": "boom", "run_id": "r"},
    ],
)
async def test_flows_that_are_never_evaluated(flow: dict[str, Any]) -> None:
    store = InMemoryOnlineEvalStore()
    service = EvaluationService(event_bus=EventBus(), config=_config(), store=store)
    service.start()
    await service._on_flow_completed(flow)
    assert service._queue is not None and service._queue.qsize() == 0
    await service.shutdown()


async def test_unsampled_flows_are_skipped() -> None:
    store = InMemoryOnlineEvalStore()
    service = EvaluationService(
        event_bus=EventBus(), config=_config(online_sample_rate=0.0), store=store
    )
    service.start()
    for i in range(50):
        await service._on_flow_completed(_flow(run_id=f"r{i}"))
    assert service._queue is not None and service._queue.qsize() == 0
    await service.shutdown()


async def test_backpressure_drops_and_counts_without_blocking() -> None:
    from prometheus_client import REGISTRY

    gate = asyncio.Event()

    class _BlockingStore(InMemoryOnlineEvalStore):
        async def save(self, record: Any) -> None:
            await gate.wait()
            await super().save(record)

    labels = {"reason": "queue_full"}
    before = REGISTRY.get_sample_value("mas_eval_online_dropped_total", labels) or 0.0
    service = EvaluationService(
        event_bus=EventBus(),
        config=_config(online_queue_size=2, online_workers=1),
        store=_BlockingStore(),
    )
    service.start()
    # The worker takes one sample and blocks in the store; two more fill the
    # queue; the remaining seven are dropped — and the handler never waits.
    for i in range(10):
        await asyncio.wait_for(service._on_flow_completed(_flow(run_id=f"r{i}")), 0.5)
        await asyncio.sleep(0)
    after = REGISTRY.get_sample_value("mas_eval_online_dropped_total", labels) or 0.0
    assert after - before == 7
    gate.set()
    await _drain(service)
    await service.shutdown()


async def test_judged_sample_emits_evaluation_completed() -> None:
    bus = EventBus()
    recorder = _Recorder(bus, EventNames.EVALUATION_COMPLETED)
    store = InMemoryOnlineEvalStore()
    judge = _judge(0.9)
    service = EvaluationService(
        event_bus=bus,
        evaluator=judge,
        config=_config(online_judge_rate=1.0),
        store=store,
    )
    service.start()
    await service._on_flow_completed(_flow())
    await _drain(service)

    judge.evaluate.assert_awaited_once()
    (payload,) = recorder.named(EventNames.EVALUATION_COMPLETED)
    assert payload["score"] == 0.9 and payload["run_id"] == "run-1"
    assert payload["tenant_id"] == "acme" and payload["source"] == "judge"
    assert sorted(r.source for r in await store.recent()) == ["checks", "judge"]
    await service.shutdown()


async def test_judge_is_off_by_default() -> None:
    judge = _judge()
    service = EvaluationService(event_bus=EventBus(), evaluator=judge, config=_config())
    service.start()
    await service._on_flow_completed(_flow())
    await _drain(service)
    judge.evaluate.assert_not_awaited()
    await service.shutdown()


async def test_degradation_emits_event_and_sets_gauge() -> None:
    from prometheus_client import REGISTRY

    bus = EventBus()
    recorder = _Recorder(
        bus, EventNames.EVALUATION_DEGRADED, EventNames.EVALUATION_RECOVERED
    )
    service = EvaluationService(
        event_bus=bus,
        config=_config(
            online_window=5,
            online_min_samples=5,
            online_quality_floor=0.6,
            online_recovery_margin=0.1,
        ),
    )
    service.start()
    for i in range(5):
        await service.process_sample(
            OnlineSample(intent="degrade_probe", query="q", response="")
        )
    (degraded,) = recorder.named(EventNames.EVALUATION_DEGRADED)
    assert degraded["intent"] == "degrade_probe" and degraded["reason"] == "below_floor"
    gauge = {"intent": "degrade_probe", "source": "checks"}
    assert REGISTRY.get_sample_value("mas_eval_online_degraded", gauge) == 1.0

    for i in range(5):
        await service.process_sample(
            OnlineSample(intent="degrade_probe", query="q", response=GOOD)
        )
    assert len(recorder.named(EventNames.EVALUATION_RECOVERED)) == 1
    assert REGISTRY.get_sample_value("mas_eval_online_degraded", gauge) == 0.0
    await service.shutdown()


async def test_intent_labels_are_capped_for_the_monitor() -> None:
    service = EvaluationService(
        event_bus=EventBus(), config=_config(online_max_intents=2)
    )
    service.start()
    for intent in ("a", "b", "c", "d"):
        await service.process_sample(
            OnlineSample(intent=intent, query="q", response=GOOD)
        )
    assert {row["intent"] for row in service.summary()["intents"]} == {
        "a",
        "b",
        "other",
    }
    await service.shutdown()


async def test_store_failure_is_counted_not_raised() -> None:
    store = MagicMock()
    store.save = AsyncMock(side_effect=RuntimeError("db down"))
    service = EvaluationService(event_bus=EventBus(), config=_config(), store=store)
    service.start()
    await service.process_sample(OnlineSample(intent="qa", query="q", response=GOOD))
    store.save.assert_awaited_once()
    await service.shutdown()


async def test_retention_sweep() -> None:
    store = InMemoryOnlineEvalStore()
    service = EvaluationService(
        event_bus=EventBus(), config=_config(online_retention_days=1), store=store
    )
    from baselith.core.evaluation.online_store import OnlineEvalRecord

    await store.save(OnlineEvalRecord("qa", "checks", 1.0, "excellent", created_at=0.0))
    await store.save(
        OnlineEvalRecord("qa", "checks", 1.0, "excellent", created_at=200_000.0)
    )
    service.start()
    assert await service.sweep_retention(now=200_000.0) == 1
    assert len(store) == 1
    await service.shutdown()


async def test_start_spawns_workers_and_shutdown_cancels_them() -> None:
    bus = EventBus()
    service = EvaluationService(
        event_bus=bus,
        config=_config(
            online_workers=3, online_store="memory", online_retention_days=7
        ),
    )
    service.start()
    tasks = service._background_tasks()
    assert len(tasks) == 4  # three workers + the retention sweeper
    assert len(bus._get_handlers(EventNames.FLOW_COMPLETED)) == 1

    await service.shutdown()
    assert all(task.done() for task in tasks)
    assert service._background_tasks() == []
    assert len(bus._get_handlers(EventNames.FLOW_COMPLETED)) == 0
    assert service.running is False

    service.start()  # restartable after a stop
    assert len(service._background_tasks()) == 4
    await service.shutdown()


async def test_lazy_registry_teardown_prefers_shutdown() -> None:
    from baselith.core.di.lazy_registry import LazyServiceRegistry

    service = EvaluationService(event_bus=EventBus(), config=_config())
    registry = LazyServiceRegistry()

    async def factory() -> EvaluationService:
        service.start()
        return service

    registry.register_factory("evaluation", factory)
    await registry.get_or_create("evaluation")
    tasks = service._background_tasks()
    assert tasks
    await registry.shutdown_all()
    assert all(task.done() for task in tasks)
