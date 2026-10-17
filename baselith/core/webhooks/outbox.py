"""Transactional outbox for webhook events.

``WebhookService.emit`` delivers in the caller's process. When that process
dies between the business commit and the delivery (a deploy, an OOM kill, a
dropped connection) the event is gone: the data changed and no subscriber ever
heard of it. The outbox closes that gap. :meth:`WebhookService.publish` writes
the event to a durable table — in the caller's own transaction when one is
passed — and :class:`~baselith.core.webhooks.relay.OutboxRelay` delivers it
afterwards, from any replica.

Guarantees, stated plainly:

* **At least once.** A relay that dies after delivering and before deleting the
  row delivers the event again. The event id is stable across redeliveries and
  travels in the envelope (``id``), so a receiver deduplicates on it.
* **Atomic with the business write** when the caller passes its cursor: either
  both the change and the event commit, or neither does.
* **Per-endpoint retries stay with the dispatcher.** The relay guarantees the
  event is *dispatched*; each endpoint's retries, dead-lettering and replay are
  the existing delivery records. A row leaves the outbox once every endpoint
  has been attempted.
* **No ordering guarantee** across events.

This module holds the types, the store interface and the process-local store
(single node and tests). The durable implementation is
:mod:`baselith.core.webhooks.outbox_postgres`.
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "InMemoryOutbox",
    "OutboxEntry",
    "OutboxState",
    "OutboxStore",
    "retry_delay",
]

#: Longest wait before a failed row is tried again, seconds.
MAX_RETRY_DELAY_SECONDS = 300.0


class OutboxState(str, Enum):
    """Lifecycle of an outbox row (delivered rows are deleted, not kept)."""

    PENDING = "pending"
    DEAD = "dead"


class OutboxEntry(BaseModel):
    """One event waiting to be dispatched."""

    id: str
    tenant_id: str = "default"
    event_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    attempts: int = 0
    last_error: str | None = None
    state: OutboxState = OutboxState.PENDING
    #: Epoch seconds before which the row is not due (in-memory store only).
    next_attempt_at: float = 0.0
    #: Epoch seconds until which another claimant must leave the row alone.
    locked_until: float = 0.0

    model_config = ConfigDict(extra="forbid")


def retry_delay(attempts: int, base: float) -> float:
    """Seconds to wait before attempt ``attempts + 1`` (exponential, capped)."""
    exponent = max(attempts - 1, 0)
    return float(min(max(base, 0.0) * (2.0**exponent), MAX_RETRY_DELAY_SECONDS))


@runtime_checkable
class OutboxStore(Protocol):
    """Where pending events live between the commit and their dispatch."""

    async def enqueue(self, entry: OutboxEntry, cur: Any | None = None) -> None:
        """Insert ``entry``; a repeated id is ignored.

        ``cur`` is the caller's open database cursor. The durable store inserts
        through it and leaves the commit to the caller; the process-local store
        ignores it.
        """
        ...

    async def claim(self, limit: int, lease_seconds: float) -> list[OutboxEntry]:
        """Lease up to ``limit`` due rows and count the attempt on each."""
        ...

    async def complete(self, entry_id: str) -> None:
        """The row was dispatched: drop it."""
        ...

    async def fail(self, entry_id: str, error: str, retry_in: float) -> None:
        """Release the lease and schedule the next attempt ``retry_in`` s away."""
        ...

    async def bury(self, entry_id: str, error: str) -> None:
        """Park the row as dead: attempts are exhausted, an operator decides."""
        ...

    async def count(self, state: OutboxState = OutboxState.PENDING) -> int:
        """Rows in ``state`` (for metrics and tests)."""
        ...


class InMemoryOutbox:
    """Process-local outbox.

    It survives nothing: a crash loses the queue, so it only buys the relay's
    retry behaviour, not durability. Use ``WEBHOOK_STORE=postgres`` for that.
    """

    def __init__(self) -> None:
        self._rows: dict[str, OutboxEntry] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, entry: OutboxEntry, cur: Any | None = None) -> None:
        async with self._lock:
            self._rows.setdefault(entry.id, entry.model_copy())

    async def claim(self, limit: int, lease_seconds: float) -> list[OutboxEntry]:
        now = time.time()
        async with self._lock:
            due = sorted(
                (
                    row
                    for row in self._rows.values()
                    if row.state is OutboxState.PENDING
                    and row.next_attempt_at <= now
                    and row.locked_until <= now
                ),
                key=lambda row: row.next_attempt_at,
            )[: max(limit, 0)]
            for row in due:
                row.attempts += 1
                row.locked_until = now + lease_seconds
            return [row.model_copy() for row in due]

    async def complete(self, entry_id: str) -> None:
        async with self._lock:
            self._rows.pop(entry_id, None)

    async def fail(self, entry_id: str, error: str, retry_in: float) -> None:
        async with self._lock:
            row = self._rows.get(entry_id)
            if row is not None:
                row.last_error = error
                row.locked_until = 0.0
                row.next_attempt_at = time.time() + max(retry_in, 0.0)

    async def bury(self, entry_id: str, error: str) -> None:
        async with self._lock:
            row = self._rows.get(entry_id)
            if row is not None:
                row.last_error = error
                row.locked_until = 0.0
                row.state = OutboxState.DEAD

    async def count(self, state: OutboxState = OutboxState.PENDING) -> int:
        return sum(1 for row in self._rows.values() if row.state is state)
