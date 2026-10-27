"""Quality transitions reach operators: event bus, webhooks, and the service wiring."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

import baselith.core.config.webhooks as webhook_cfg
import baselith.core.webhooks.service as webhook_service
from baselith.core.config.evaluation import EvaluationConfig
from baselith.core.config.webhooks import WebhookConfig
from baselith.core.evaluation.online_alerts import (
    SCOPE_FLEET,
    WEBHOOK_EVENT_TYPES,
    TransitionAnnouncer,
)
from baselith.core.evaluation.online_checks import OnlineSample
from baselith.core.evaluation.online_monitor import DEGRADED, MonitorTransition
from baselith.core.evaluation.service import EvaluationService
from baselith.core.events import EventBus, EventNames
from baselith.core.webhooks.dispatcher import WebhookDispatcher
from baselith.core.webhooks.service import WebhookService
from baselith.core.webhooks.signing import SIGNATURE_HEADER, verify_signature
from baselith.core.webhooks.store import InMemoryWebhookStore
from tests.unit.core.evaluation.fleet_fakes import FakeRedis, policy, worker

HOOK = "https://hooks.test/quality"


def _transition(state: str = DEGRADED) -> MonitorTransition:
    return MonitorTransition(
        intent="qa",
        source="checks",
        state=state,
        window_mean=0.2,
        reference_mean=None,
        floor=0.5,
        samples=10,
        reason="below_floor" if state == DEGRADED else "recovered",
    )


class _Hooks:
    """A real WebhookService whose HTTP goes to an in-memory receiver."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True):
        self.received: list[httpx.Request] = []
        config = WebhookConfig(
            WEBHOOKS_ENABLED=enabled,
            WEBHOOK_ALLOW_INTERNAL=True,
            WEBHOOK_RETRY_BACKOFF_SECONDS=0,
            WEBHOOK_MAX_ATTEMPTS=1,
        )
        store = InMemoryWebhookStore()

        def handler(request: httpx.Request) -> httpx.Response:
            self.received.append(request)
            return httpx.Response(200)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.service = WebhookService(
            store=store,
            config=config,
            dispatcher=WebhookDispatcher(store, config, http_client=client),
        )
        monkeypatch.setattr(webhook_cfg, "get_webhook_config", lambda: config)
        monkeypatch.setattr(
            webhook_service, "get_webhook_service", lambda: self.service
        )

    async def subscribe(self, tenant: str, *events: str) -> None:
        await self.service.register_endpoint(
            HOOK, "whsec_ops", tenant_id=tenant, event_types=set(events)
        )

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.received]


def test_the_webhook_event_types_are_the_event_names() -> None:
    assert WEBHOOK_EVENT_TYPES == ("evaluation.degraded", "evaluation.recovered")


async def test_degradation_is_delivered_to_the_operators_tenant(monkeypatch) -> None:
    hooks = _Hooks(monkeypatch)
    await hooks.subscribe("ops", "evaluation.degraded")
    await hooks.subscribe("acme", "*")  # another tenant: not an operator
    bus = EventBus()
    seen: list[dict[str, Any]] = []

    async def on_degraded(data: dict[str, Any]) -> None:
        seen.append(data)

    bus.subscribe(EventNames.EVALUATION_DEGRADED, on_degraded)
    announcer = TransitionAnnouncer(bus, webhook_tenant="ops")
    await announcer.announce(_transition(), SCOPE_FLEET)
    await announcer.drain()

    (request,) = hooks.received
    body = json.loads(request.content)
    assert body["type"] == "evaluation.degraded"
    assert body["tenant_id"] == "ops"
    assert body["data"] == seen[0]
    assert body["data"]["scope"] == "fleet"
    assert body["data"]["state"] == "degraded"
    assert body["data"]["intent"] == "qa"
    assert verify_signature(
        "whsec_ops", request.content, request.headers[SIGNATURE_HEADER]
    )


async def test_subscribers_choose_the_transition(monkeypatch) -> None:
    hooks = _Hooks(monkeypatch)
    await hooks.subscribe("default", "evaluation.recovered")
    announcer = TransitionAnnouncer(EventBus())
    await announcer.announce(_transition(DEGRADED), SCOPE_FLEET)
    await announcer.announce(_transition("recovered"), SCOPE_FLEET)
    await announcer.drain()
    assert [b["type"] for b in hooks.bodies()] == ["evaluation.recovered"]


async def test_webhooks_off_means_no_delivery_task(monkeypatch) -> None:
    hooks = _Hooks(monkeypatch, enabled=False)
    await hooks.subscribe("default", "*")
    announcer = TransitionAnnouncer(EventBus())
    await announcer.announce(_transition(), SCOPE_FLEET)
    assert not announcer._pending
    assert hooks.received == []


async def test_a_slow_subscriber_does_not_hold_the_announcer(monkeypatch) -> None:
    release = asyncio.Event()

    class _Slow:
        async def publish(self, *_: Any, **__: Any) -> list[Any]:
            await release.wait()
            return []

    monkeypatch.setattr(
        webhook_cfg, "get_webhook_config", lambda: WebhookConfig(WEBHOOKS_ENABLED=True)
    )
    monkeypatch.setattr(webhook_service, "get_webhook_service", lambda: _Slow())
    announcer = TransitionAnnouncer(EventBus())
    await asyncio.wait_for(announcer.announce(_transition(), SCOPE_FLEET), 1)
    assert len(announcer._pending) == 1
    release.set()
    await announcer.drain()


async def test_a_failing_webhook_service_is_counted(monkeypatch) -> None:
    from baselith.core.observability.metrics import EVAL_ONLINE_FAILURES_TOTAL

    class _Broken:
        async def publish(self, *_: Any, **__: Any) -> list[Any]:
            raise RuntimeError("store down")

    monkeypatch.setattr(
        webhook_cfg, "get_webhook_config", lambda: WebhookConfig(WEBHOOKS_ENABLED=True)
    )
    monkeypatch.setattr(webhook_service, "get_webhook_service", lambda: _Broken())
    counter = EVAL_ONLINE_FAILURES_TOTAL.labels(stage="webhook")
    before = counter._value.get()
    announcer = TransitionAnnouncer(EventBus())
    await announcer.announce(_transition(), SCOPE_FLEET)
    await announcer.drain()
    assert counter._value.get() == before + 1


# -- end to end: two workers, one webhook --------------------------------------


async def test_fleet_degradation_reaches_the_webhook_once(monkeypatch) -> None:
    hooks = _Hooks(monkeypatch)
    await hooks.subscribe("default", *WEBHOOK_EVENT_TYPES)
    redis = FakeRedis()
    announcers = [TransitionAnnouncer(EventBus()) for _ in range(2)]
    workers = []
    for i, announcer in enumerate(announcers):
        w = worker(redis, [], f"w{i}")

        async def announce(t: MonitorTransition, a: TransitionAnnouncer = announcer):
            await a.announce(t, SCOPE_FLEET)

        w._announce = announce
        workers.append(w)

    for i in range(10):
        await workers[i % 2].record("e2e", "checks", 0.0)
    for _ in range(2):
        for w in workers:
            await w.tick()
    for announcer in announcers:
        await announcer.drain()

    assert [(b["type"], b["data"]["intent"]) for b in hooks.bodies()] == [
        ("evaluation.degraded", "e2e")
    ]


# -- the service: fleet mode announces nothing locally -------------------------


def _service(redis: FakeRedis, announced: list[Any], **kw: Any) -> EvaluationService:
    shared = policy()
    config = EvaluationConfig(
        enabled=True, online_sample_rate=1.0, online_retention_days=0, **kw
    )
    return EvaluationService(
        event_bus=EventBus(),
        config=config,
        monitor=shared,
        fleet=worker(redis, announced, "svc", monitor=shared),
    )


async def test_fleet_mode_records_and_never_announces_per_worker() -> None:
    redis, announced = FakeRedis(), []
    service = _service(redis, announced)
    service.start()
    events: list[Any] = []

    async def record(data: dict[str, Any]) -> None:
        events.append(data)

    service.event_bus.subscribe(EventNames.EVALUATION_DEGRADED, record)
    for _ in range(10):
        await service.process_sample(OnlineSample(intent="svc", query="q", response=""))
    # The local monitor degraded too, but in fleet mode only the leader speaks.
    assert service.monitor is not None
    assert service.monitor.summary()[0]["degraded"] is True
    assert events == []

    summary = await service.online_summary()
    assert summary["scope"] == "fleet"
    (row,) = summary["intents"]
    assert (row["intent"], row["window_samples"]) == ("svc", 10)
    await service.shutdown()


async def test_summary_falls_back_to_the_worker_when_redis_is_away() -> None:
    redis, announced = FakeRedis(), []
    service = _service(redis, announced)
    service.start()
    await service.process_sample(OnlineSample(intent="svc2", query="q", response=""))
    redis.fail = True
    summary = await service.online_summary()
    assert summary["scope"] == "worker"
    assert [r["intent"] for r in summary["intents"]] == ["svc2"]
    await service.shutdown()


async def test_without_a_fleet_the_worker_announces_with_worker_scope(
    monkeypatch,
) -> None:
    hooks = _Hooks(monkeypatch)
    await hooks.subscribe("ops", "evaluation.degraded")
    service = EvaluationService(
        event_bus=EventBus(),
        config=EvaluationConfig(
            enabled=True,
            online_sample_rate=1.0,
            online_retention_days=0,
            online_fleet=False,
            online_webhook_tenant="ops",
        ),
        monitor=policy(),
    )
    service.start()
    for _ in range(10):
        await service.process_sample(
            OnlineSample(intent="solo", query="q", response="")
        )
    await service.shutdown()
    assert service.fleet is None
    (body,) = hooks.bodies()
    assert (body["tenant_id"], body["data"]["scope"]) == ("ops", "worker")
    assert (await service.online_summary())["scope"] == "worker"
