"""The webhook transactional outbox: queueing, the relay, and the Postgres SQL.

The delivery guarantees under test are the ones the module docstring promises:
an event queued survives a failed dispatch, a stuck row is retried with
backoff then parked, and a duplicate enqueue is ignored.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from pydantic import SecretStr

from baselith.core.config.webhooks import WebhookConfig
from baselith.core.webhooks.dispatcher import WebhookDispatcher
from baselith.core.webhooks.outbox import (
    InMemoryOutbox,
    OutboxEntry,
    OutboxState,
    retry_delay,
)
from baselith.core.webhooks.outbox_postgres import PostgresOutbox
from baselith.core.webhooks.relay import OutboxRelay
from baselith.core.webhooks.service import WebhookService
from baselith.core.webhooks.store import InMemoryWebhookStore
from baselith.core.webhooks.types import WebhookEndpoint


def _config(**kw: Any) -> WebhookConfig:
    base: dict[str, Any] = {
        "WEBHOOKS_ENABLED": True,
        "WEBHOOK_OUTBOX_ENABLED": True,
        "WEBHOOK_ALLOW_INTERNAL": True,
        "WEBHOOK_RETRY_BACKOFF_SECONDS": 0,
        "WEBHOOK_MAX_ATTEMPTS": 1,
    }
    base.update(kw)
    return WebhookConfig(**base)


async def _service(handler: Any, **kw: Any) -> tuple[WebhookService, InMemoryOutbox]:
    cfg = _config(**kw)
    store = InMemoryWebhookStore()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = WebhookService(
        store=store, config=cfg, dispatcher=WebhookDispatcher(store, cfg, client)
    )
    await store.add_endpoint(
        WebhookEndpoint(url="https://hooks.test/in", secret=SecretStr("whsec"))
    )
    outbox = InMemoryOutbox()
    service._outbox = outbox
    return service, outbox


def _ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200)


def _relay(service: WebhookService, outbox: InMemoryOutbox, **kw: Any) -> OutboxRelay:
    return OutboxRelay(service, outbox, backoff_seconds=0.0, **kw)


class TestPublish:
    async def test_queues_instead_of_delivering(self) -> None:
        sent: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            sent.append(req)
            return httpx.Response(200)

        service, outbox = await _service(handler)
        event_id = await service.publish("agent.completed", {"x": 1})
        assert event_id is not None
        assert await outbox.count() == 1
        assert sent == []  # nothing left the process yet

    async def test_without_the_outbox_it_is_a_plain_emit(self) -> None:
        sent: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            sent.append(req)
            return httpx.Response(200)

        service, outbox = await _service(handler, WEBHOOK_OUTBOX_ENABLED=False)
        assert await service.publish("agent.completed", {}) is None
        assert len(sent) == 1
        assert await outbox.count() == 0

    async def test_disabled_webhooks_queue_nothing(self) -> None:
        service, outbox = await _service(_ok, WEBHOOKS_ENABLED=False)
        assert await service.publish("agent.completed", {}) is None
        assert await outbox.count() == 0

    async def test_duplicate_enqueue_is_ignored(self) -> None:
        outbox = InMemoryOutbox()
        entry = OutboxEntry(id="evt_1", event_type="a.b")
        await outbox.enqueue(entry)
        await outbox.enqueue(entry)
        assert await outbox.count() == 1


class TestRelay:
    async def test_delivers_with_the_stable_event_id_then_deletes(self) -> None:
        bodies: list[dict[str, Any]] = []

        def handler(req: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(req.content))
            return httpx.Response(200)

        service, outbox = await _service(handler)
        event_id = await service.publish("agent.completed", {"n": 7})
        assert await _relay(service, outbox).drain_once() == 1
        assert bodies[0]["id"] == event_id
        assert bodies[0]["data"] == {"n": 7}
        assert await outbox.count() == 0

    async def test_a_failing_store_reschedules_the_row(self) -> None:
        service, outbox = await _service(_ok)
        await service.publish("agent.completed", {})
        with patch.object(
            service._store, "endpoints_for_event", AsyncMock(side_effect=OSError("db"))
        ):
            await _relay(service, outbox).drain_once()
        assert await outbox.count() == 1
        row = next(iter(outbox._rows.values()))
        assert row.last_error and "db" in row.last_error
        # retried and delivered once the store is back
        assert await _relay(service, outbox).drain_once() == 1
        assert await outbox.count() == 0

    async def test_exhausted_attempts_park_the_row_as_dead(self) -> None:
        service, outbox = await _service(_ok)
        await service.publish("agent.completed", {})
        relay = _relay(service, outbox, max_attempts=2)
        with patch.object(
            service._store, "endpoints_for_event", AsyncMock(side_effect=OSError("db"))
        ):
            for _ in range(3):
                await relay.drain_once()
        assert await outbox.count(OutboxState.DEAD) == 1
        assert await outbox.count() == 0
        assert await relay.drain_once() == 0  # a dead row is never claimed again

    async def test_a_leased_row_is_not_claimed_twice(self) -> None:
        outbox = InMemoryOutbox()
        await outbox.enqueue(OutboxEntry(id="evt_1", event_type="a.b"))
        assert len(await outbox.claim(10, lease_seconds=60)) == 1
        assert await outbox.claim(10, lease_seconds=60) == []

    async def test_an_endpoint_failure_does_not_keep_the_row(self) -> None:
        # Per-endpoint retries and dead-lettering are the dispatcher's job; the
        # row only guarantees the event was dispatched.
        service, outbox = await _service(lambda _: httpx.Response(500))
        await service.publish("agent.completed", {})
        await _relay(service, outbox).drain_once()
        assert await outbox.count() == 0
        failed = await service.store.list_deliveries("default")
        assert [d.status.value for d in failed] == ["failed"]


class TestRetryDelay:
    def test_doubles_and_is_capped(self) -> None:
        assert retry_delay(1, 2.0) == 2.0
        assert retry_delay(3, 2.0) == 8.0
        assert retry_delay(50, 2.0) == 300.0

    def test_zero_base_never_waits(self) -> None:
        assert retry_delay(5, 0.0) == 0.0


class TestPostgresOutbox:
    async def test_enqueue_inside_the_callers_transaction_uses_its_cursor(
        self,
    ) -> None:
        cur = MagicMock()
        cur.execute = AsyncMock()
        with patch("baselith.core.webhooks.outbox_postgres.get_async_cursor") as own:
            await PostgresOutbox().enqueue(
                OutboxEntry(id="evt_1", event_type="a.b", data={"k": 1}), cur
            )
        own.assert_not_called()  # no second connection, no separate commit
        sql, params = cur.execute.await_args.args
        assert "INSERT INTO webhook_outbox" in sql
        assert "ON CONFLICT (id) DO NOTHING" in sql
        assert params[0] == "evt_1" and json.loads(params[3]) == {"k": 1}

    async def test_claim_is_skip_locked_on_the_database_clock(self) -> None:
        cur = MagicMock()
        cur.execute = AsyncMock()
        cur.fetchall = AsyncMock(return_value=[])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=cur)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch(
            "baselith.core.webhooks.outbox_postgres.get_async_cursor",
            return_value=ctx,
        ):
            assert await PostgresOutbox().claim(5, 90.0) == []
        sql, params = cur.execute.await_args.args
        assert "FOR UPDATE SKIP LOCKED" in sql
        assert "clock_timestamp()" in sql
        assert params == (5, 90.0)
