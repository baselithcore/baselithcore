"""Integration test: the Postgres webhook store round-trips against a real server.

Runs only with the real-database opt-in::

    docker compose up -d postgres
    BASELITH_TEST_REAL_DB=1 python -m pytest tests/integration/test_webhook_store_postgres.py

The tables are created by running ``upgrade()`` of migration 011 itself, so a
column renamed there and not in the store fails here.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from tests.integration.test_tool_ledger_postgres import (
    _conninfo,
    _pg_available,
    _real_cursor_factory,
    _real_db_enabled,
)

pytestmark = [pytest.mark.integration]

MODULE = "core.webhooks.store_postgres"
MIGRATION = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "011_webhooks.py"
)


class _Op:
    def __init__(self, cur: Any) -> None:
        self._cur = cur

    def execute(self, sql: str) -> None:
        self._cur.execute(sql)


@pytest.fixture
def tenant() -> Iterator[str]:
    if not _real_db_enabled():
        pytest.skip("set BASELITH_TEST_REAL_DB=1 to run against a real Postgres")
    if not _pg_available():
        pytest.skip("PostgreSQL not reachable (docker compose up -d postgres)")

    import psycopg

    spec = importlib.util.spec_from_file_location("mig_011", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    tenant_id = f"itest-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_conninfo(), autocommit=True) as conn, conn.cursor() as cur:
        migration.op = _Op(cur)
        migration.upgrade()

    yield tenant_id

    with psycopg.connect(_conninfo(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM webhook_deliveries WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM webhook_endpoints WHERE tenant_id = %s", (tenant_id,))


@pytest.fixture
def store(monkeypatch, tenant: str) -> Any:
    from core.webhooks.store_postgres import PostgresWebhookStore

    monkeypatch.setattr(f"{MODULE}.get_async_cursor", _real_cursor_factory())
    return PostgresWebhookStore()


def _endpoint(tenant: str, **extra: Any) -> Any:
    from core.webhooks.types import WebhookEndpoint

    fields: dict[str, Any] = {
        "tenant_id": tenant,
        "url": "https://hooks.example.com/in",
        "secret": SecretStr("whsec_live_value"),
        "event_types": {"agent.completed"},
        "headers": {"Authorization": "Bearer token-123"},
    }
    return WebhookEndpoint(**{**fields, **extra})


async def test_endpoint_round_trip_and_event_matching(store, tenant):
    endpoint = await store.add_endpoint(_endpoint(tenant))
    wildcard = await store.add_endpoint(_endpoint(tenant, event_types={"*"}))

    loaded = await store.get_endpoint(endpoint.id)
    assert loaded is not None
    assert loaded.secret.get_secret_value() == "whsec_live_value"
    assert loaded.headers == {"Authorization": "Bearer token-123"}
    assert loaded.event_types == {"agent.completed"}

    assert await store.count_endpoints(tenant) == 2
    matching = await store.endpoints_for_event(tenant, "agent.completed")
    assert {e.id for e in matching} == {endpoint.id, wildcard.id}
    only_wildcard = await store.endpoints_for_event(tenant, "agent.failed")
    assert [e.id for e in only_wildcard] == [wildcard.id]

    assert await store.delete_endpoint(endpoint.id) is True
    assert await store.get_endpoint(endpoint.id) is None


async def test_secrets_are_ciphertext_at_rest_when_keys_are_set(
    monkeypatch, store, tenant
):
    import psycopg

    from core.security.encryption import FieldEncryptor

    encryptor = FieldEncryptor.from_keys({"k1": "a" * 32}, active_key_id="k1")
    monkeypatch.setattr("core.security.get_field_encryptor", lambda: encryptor)

    endpoint = await store.add_endpoint(_endpoint(tenant))

    with psycopg.connect(_conninfo()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT secret, headers FROM webhook_endpoints WHERE id = %s",
            (endpoint.id,),
        )
        secret, headers = cur.fetchone()
    assert FieldEncryptor.is_encrypted(secret) and "whsec" not in secret
    assert FieldEncryptor.is_encrypted(headers) and "token-123" not in headers

    loaded = await store.get_endpoint(endpoint.id)
    assert loaded is not None
    assert loaded.secret.get_secret_value() == "whsec_live_value"


async def test_deliveries_are_recorded_updated_and_listed(store, tenant):
    from core.webhooks.types import DeliveryStatus, WebhookDelivery

    delivery = WebhookDelivery(
        endpoint_id="whe_x",
        event_id="evt_1",
        event_type="agent.completed",
        tenant_id=tenant,
        url="https://hooks.example.com/in",
        payload={"id": "evt_1"},
    )
    await store.record_delivery(delivery)
    delivery.status = DeliveryStatus.SUCCESS
    delivery.attempts = 2
    await store.record_delivery(delivery)

    loaded = await store.get_delivery(delivery.id)
    assert loaded is not None
    assert loaded.status is DeliveryStatus.SUCCESS
    assert loaded.attempts == 2
    assert loaded.payload == {"id": "evt_1"}
    assert [d.id for d in await store.list_deliveries(tenant)] == [delivery.id]
