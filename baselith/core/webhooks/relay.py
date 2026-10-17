"""The outbox relay: dispatches queued webhook events, from any replica.

One relay runs in every API process when ``WEBHOOK_OUTBOX_ENABLED`` is on. They
do not coordinate: the store's lease (``FOR UPDATE SKIP LOCKED``) keeps two of
them off the same row, and a relay that dies mid-delivery simply lets its lease
lapse so another one picks the row up. See :mod:`baselith.core.webhooks.outbox`
for the delivery guarantees.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from baselith.core.observability.logging import get_logger
from baselith.core.webhooks.outbox import OutboxEntry, OutboxStore, retry_delay
from baselith.core.webhooks.types import WebhookEvent

if TYPE_CHECKING:
    from baselith.core.webhooks.service import WebhookService

logger = get_logger(__name__)

__all__ = ["OutboxRelay"]


class OutboxRelay:
    """Poll the outbox and hand each due event to the webhook service."""

    def __init__(
        self,
        service: WebhookService,
        store: OutboxStore,
        *,
        poll_seconds: float = 2.0,
        batch_size: int = 50,
        lease_seconds: float = 120.0,
        max_attempts: int = 8,
        backoff_seconds: float = 2.0,
    ) -> None:
        self._service = service
        self._store = store
        self._poll = poll_seconds
        self._batch = batch_size
        self._lease = lease_seconds
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def drain_once(self) -> int:
        """Claim one batch and dispatch it; return how many rows were handled."""
        from baselith.core.db.session_setup import system_tenant_scope

        # The relay serves every tenant: with RLS on it must say so.
        with system_tenant_scope():
            entries = await self._store.claim(self._batch, self._lease)
            if not entries:
                return 0
            await asyncio.gather(*(self._dispatch(entry) for entry in entries))
        return len(entries)

    async def _dispatch(self, entry: OutboxEntry) -> None:
        if entry.attempts > self._max_attempts:
            logger.error(
                "webhook_outbox_dead",
                extra={"event_id": entry.id, "attempts": entry.attempts},
            )
            await self._store.bury(
                entry.id, entry.last_error or "attempts exhausted before delivery"
            )
            return
        event = WebhookEvent(
            id=entry.id,
            type=entry.event_type,
            tenant_id=entry.tenant_id,
            created_at=entry.created_at,
            data=entry.data,
        )
        try:
            await self._service.deliver_event(event)
        except Exception as exc:
            delay = retry_delay(entry.attempts, self._backoff)
            logger.warning(
                "webhook_outbox_retry",
                extra={
                    "event_id": entry.id,
                    "attempts": entry.attempts,
                    "retry_in": delay,
                    "error": str(exc),
                },
            )
            await self._store.fail(entry.id, f"{type(exc).__name__}: {exc}", delay)
            return
        await self._store.complete(entry.id)

    async def run(self) -> None:
        """Drain until :meth:`stop`; a failing poll is logged, never fatal."""
        while not self._stopping.is_set():
            handled = 0
            try:
                handled = await self.drain_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("webhook_outbox_poll_failed: %s", exc)
            if handled >= self._batch:
                continue  # a full batch means more is probably waiting
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=self._poll)

    async def start(self) -> None:
        """Start the background loop (idempotent)."""
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self.run(), name="webhook-outbox-relay")

    async def stop(self) -> None:
        """Stop the loop; a delivery in flight finishes or its lease lapses."""
        self._stopping.set()
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
