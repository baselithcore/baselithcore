"""Postgres-backed :class:`~core.webhooks.store.WebhookStore`.

The in-memory store keeps subscriptions in one process: they vanish on
restart, and a second replica — or the RQ worker, which emits the ``agent.*``
events — never sees them, so an endpoint registered through the API could
silently never fire. Select this store with ``WEBHOOK_STORE=postgres``.

Schema ownership: ``webhook_endpoints`` and ``webhook_deliveries`` are created
by ``migrations/versions/011_webhooks.py`` and by nothing else; this module
runs no DDL. Rows are tenant-scoped under the same row-level-security policy
as the other tenant tables.

Secrets at rest: an endpoint's signing ``secret`` and its ``headers`` (which
routinely carry an ``Authorization`` value) are encrypted with the process
field encryptor (``DATA_ENCRYPTION_KEYS``), bound to the endpoint id as
associated data so a ciphertext cannot be moved to another row. Without keys
they are stored as written, and the store says so once at first use.
"""

from __future__ import annotations

import json
from typing import Any

from psycopg.rows import dict_row
from pydantic import SecretStr

from core.db.connection import get_async_cursor
from core.observability.logging import get_logger
from core.webhooks.types import DeliveryStatus, WebhookDelivery, WebhookEndpoint

logger = get_logger(__name__)

__all__ = ["PostgresWebhookStore"]

_UPSERT_ENDPOINT = """
INSERT INTO webhook_endpoints
    (id, tenant_id, url, secret, event_types, enabled, description, headers, created_at)
VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    url = EXCLUDED.url,
    secret = EXCLUDED.secret,
    event_types = EXCLUDED.event_types,
    enabled = EXCLUDED.enabled,
    description = EXCLUDED.description,
    headers = EXCLUDED.headers
"""

_SELECT_ENDPOINTS = """
SELECT id, tenant_id, url, secret, event_types, enabled, description, headers, created_at
FROM webhook_endpoints
"""

_ENDPOINT_BY_ID = _SELECT_ENDPOINTS + "WHERE id = %s"
_ENDPOINTS_BY_TENANT = _SELECT_ENDPOINTS + "WHERE tenant_id = %s ORDER BY created_at"
_ENDPOINTS_FOR_EVENT = (
    _SELECT_ENDPOINTS
    + "WHERE tenant_id = %s AND enabled "
    + "AND (event_types ? '*' OR event_types ? %s) ORDER BY created_at"
)

_UPSERT_DELIVERY = """
INSERT INTO webhook_deliveries
    (id, endpoint_id, event_id, event_type, tenant_id, url, status, attempts,
       last_status_code, last_error, created_at, completed_at, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT (id) DO UPDATE SET
    status = EXCLUDED.status,
    attempts = EXCLUDED.attempts,
    last_status_code = EXCLUDED.last_status_code,
    last_error = EXCLUDED.last_error,
    completed_at = EXCLUDED.completed_at
"""

_SELECT_DELIVERIES = """
SELECT id, endpoint_id, event_id, event_type, tenant_id, url, status, attempts,
       last_status_code, last_error, created_at, completed_at, payload
FROM webhook_deliveries
"""

_DELIVERY_BY_ID = _SELECT_DELIVERIES + "WHERE id = %s"
_DELIVERIES_BY_TENANT = (
    _SELECT_DELIVERIES + "WHERE tenant_id = %s ORDER BY created_at DESC LIMIT %s"
)


class PostgresWebhookStore:
    """Durable webhook subscriptions and delivery records."""

    def __init__(self) -> None:
        self._warned_plaintext = False

    # -- secrets at rest -------------------------------------------------

    def _seal(self, endpoint_id: str, value: str) -> str:
        from core.security import get_field_encryptor

        encryptor = get_field_encryptor()
        if encryptor is None:
            if not self._warned_plaintext:
                logger.warning(
                    "webhook_secrets_stored_unencrypted",
                    remedy="set DATA_ENCRYPTION_KEYS to encrypt webhook secrets",
                )
                self._warned_plaintext = True
            return value
        return encryptor.encrypt_bytes(
            value.encode("utf-8"), aad=f"webhook:{endpoint_id}".encode()
        )

    @staticmethod
    def _open(endpoint_id: str, stored: str) -> str:
        from core.security import get_field_encryptor
        from core.security.encryption import FieldEncryptor

        encryptor = get_field_encryptor()
        if encryptor is None:
            if FieldEncryptor.is_encrypted(stored):
                # Handing the token back as the secret would sign every
                # delivery with the wrong key, silently.
                raise RuntimeError(
                    f"Webhook endpoint {endpoint_id} holds an encrypted value "
                    "but DATA_ENCRYPTION_KEYS is not configured"
                )
            return stored
        # Non-token values pass through, so rows written before the keys were
        # configured keep reading.
        return encryptor.decrypt(stored, aad=f"webhook:{endpoint_id}".encode())

    def _endpoint_from_row(self, row: dict[str, Any]) -> WebhookEndpoint:
        endpoint_id = row["id"]
        return WebhookEndpoint(
            id=endpoint_id,
            tenant_id=row["tenant_id"],
            url=row["url"],
            secret=SecretStr(self._open(endpoint_id, row["secret"])),
            event_types=set(row["event_types"] or []),
            enabled=row["enabled"],
            description=row["description"],
            headers=json.loads(self._open(endpoint_id, row["headers"]) or "{}"),
            created_at=row["created_at"],
        )

    @staticmethod
    def _delivery_from_row(row: dict[str, Any]) -> WebhookDelivery:
        return WebhookDelivery(
            id=row["id"],
            endpoint_id=row["endpoint_id"],
            event_id=row["event_id"],
            event_type=row["event_type"],
            tenant_id=row["tenant_id"],
            url=row["url"],
            status=DeliveryStatus(row["status"]),
            attempts=row["attempts"],
            last_status_code=row["last_status_code"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            payload=row["payload"] or {},
        )

    # -- endpoints -------------------------------------------------------

    async def add_endpoint(self, endpoint: WebhookEndpoint) -> WebhookEndpoint:
        async with get_async_cursor() as cur:
            await cur.execute(
                _UPSERT_ENDPOINT,
                (
                    endpoint.id,
                    endpoint.tenant_id,
                    endpoint.url,
                    self._seal(endpoint.id, endpoint.secret.get_secret_value()),
                    json.dumps(sorted(endpoint.event_types)),
                    endpoint.enabled,
                    endpoint.description,
                    self._seal(endpoint.id, json.dumps(endpoint.headers)),
                    endpoint.created_at,
                ),
            )
        return endpoint

    async def _endpoints(self, query: str, params: tuple[Any, ...]) -> list[Any]:
        async with get_async_cursor(row_factory=dict_row) as cur:
            await cur.execute(query, params)
            rows = await cur.fetchall()
        return [self._endpoint_from_row(row) for row in rows]

    async def get_endpoint(self, endpoint_id: str) -> WebhookEndpoint | None:
        found = await self._endpoints(_ENDPOINT_BY_ID, (endpoint_id,))
        return found[0] if found else None

    async def list_endpoints(self, tenant_id: str) -> list[WebhookEndpoint]:
        return await self._endpoints(_ENDPOINTS_BY_TENANT, (tenant_id,))

    async def delete_endpoint(self, endpoint_id: str) -> bool:
        async with get_async_cursor() as cur:
            await cur.execute(
                "DELETE FROM webhook_endpoints WHERE id = %s", (endpoint_id,)
            )
            return bool(cur.rowcount)

    async def endpoints_for_event(
        self, tenant_id: str, event_type: str
    ) -> list[WebhookEndpoint]:
        return await self._endpoints(_ENDPOINTS_FOR_EVENT, (tenant_id, event_type))

    async def count_endpoints(self, tenant_id: str) -> int:
        async with get_async_cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM webhook_endpoints WHERE tenant_id = %s",
                (tenant_id,),
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    # -- deliveries ------------------------------------------------------

    async def record_delivery(self, delivery: WebhookDelivery) -> WebhookDelivery:
        async with get_async_cursor() as cur:
            await cur.execute(
                _UPSERT_DELIVERY,
                (
                    delivery.id,
                    delivery.endpoint_id,
                    delivery.event_id,
                    delivery.event_type,
                    delivery.tenant_id,
                    delivery.url,
                    delivery.status.value,
                    delivery.attempts,
                    delivery.last_status_code,
                    delivery.last_error,
                    delivery.created_at,
                    delivery.completed_at,
                    json.dumps(delivery.payload),
                ),
            )
        return delivery

    async def get_delivery(self, delivery_id: str) -> WebhookDelivery | None:
        async with get_async_cursor(row_factory=dict_row) as cur:
            await cur.execute(_DELIVERY_BY_ID, (delivery_id,))
            row = await cur.fetchone()
        return self._delivery_from_row(row) if row else None

    async def list_deliveries(
        self, tenant_id: str, *, limit: int = 50
    ) -> list[WebhookDelivery]:
        async with get_async_cursor(row_factory=dict_row) as cur:
            await cur.execute(_DELIVERIES_BY_TENANT, (tenant_id, limit))
            rows = await cur.fetchall()
        return [self._delivery_from_row(row) for row in rows]

    async def purge_deliveries_before(self, cutoff: float) -> int:
        """Drop delivery records created before *cutoff* (unix seconds).

        Deliveries are an operator's replay window, not an audit log; this is
        the retention hook a scheduler or ``baselith`` command calls.
        """
        async with get_async_cursor() as cur:
            await cur.execute(
                "DELETE FROM webhook_deliveries WHERE created_at < %s", (cutoff,)
            )
            return int(cur.rowcount or 0)
