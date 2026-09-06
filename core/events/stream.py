"""Durable event log behind the in-process :class:`~core.events.bus.EventBus`.

The bus delivers an event to the handlers registered *in this process*, right
now. That is the correct default for the couplings it was built for, and it has
two properties a distributed deployment cannot live with:

* **A crash loses the event.** A handler scheduled with ``wait=False`` is an
  ``asyncio`` task; a worker killed mid-flight takes it with no record that it
  existed.
* **Events do not cross replicas.** Two pods each have their own bus, so an
  event emitted on one is invisible to a handler that only runs on the other.

A stream fixes both by writing the event down before anyone handles it. Records
are appended to an ordered, replayable log; consumers read through a **consumer
group**, which hands each record to exactly one member and keeps it *pending*
until that member acknowledges. A consumer that dies leaves its records pending
rather than consumed, and :meth:`EventStream.reclaim` hands them to a live
member — at-least-once delivery, which is why the tool ledger in
:mod:`core.orchestration.idempotency` exists alongside it.

This module is the contract plus the in-process implementation. The Redis
Streams backend lives in :mod:`core.events.stream_redis`, and the bridge that
connects a stream to a local bus in :mod:`core.events.durable`.

Ordering is per-stream and consumers are cooperative, not transactional: a
record acknowledged after its handler returns may still have run twice if the
process died between the two. Handlers must be idempotent — that is the
standing requirement of every at-least-once system, and stating it here is
cheaper than pretending otherwise.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.events.types import Event

__all__ = [
    "DEFAULT_STREAM_MAXLEN",
    "EventStream",
    "InMemoryEventStream",
    "StreamRecord",
    "StreamStats",
    "decode_event_fields",
    "encode_event_fields",
]

#: How many records a stream keeps before the oldest are trimmed. A log this
#: size is a redelivery buffer, not an archive — the audit trail lives in
#: :mod:`core.observability.audit_chain`.
DEFAULT_STREAM_MAXLEN = 10_000


@dataclass(frozen=True)
class StreamRecord:
    """One appended event, with the id used to acknowledge it."""

    id: str
    event: Event
    tenant_id: str = "default"
    #: How many times this record has been delivered. A record that keeps
    #: coming back is poisonous, and the consumer needs the count to say so.
    delivery_count: int = 1


def encode_event_fields(event: Event, tenant_id: str) -> dict[str, str]:
    """Flatten an event into the ``str -> str`` map a stream entry holds.

    ``data`` is JSON with a ``str`` fallback: an event carrying something
    unserialisable must still be recorded — losing the event entirely is worse
    than recording an approximated payload.
    """
    return {
        "name": event.name,
        "data": json.dumps(event.data, default=str),
        "source": event.source or "",
        "correlation_id": event.correlation_id or "",
        "timestamp": str(event.timestamp),
        "tenant_id": tenant_id,
    }


def decode_event_fields(fields: dict[str, str]) -> tuple[Event, str]:
    """Rebuild ``(event, tenant_id)`` from a stream entry.

    A malformed ``data`` payload yields an empty dict rather than an exception:
    a consumer that raises here can never acknowledge the record, so one bad
    entry would stall the whole group.
    """
    try:
        data = json.loads(fields.get("data") or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {"value": data}
    try:
        timestamp = float(fields.get("timestamp") or 0.0)
    except ValueError:
        timestamp = time.time()
    event = Event(
        name=fields.get("name") or "",
        data=data,
        timestamp=timestamp or time.time(),
        source=fields.get("source") or None,
        correlation_id=fields.get("correlation_id") or None,
    )
    return event, fields.get("tenant_id") or "default"


class EventStream(Protocol):
    """An ordered, replayable log of events with consumer-group delivery."""

    async def append(self, event: Event, *, tenant_id: str = "default") -> str:
        """Write ``event`` to the log and return its record id."""
        ...

    async def ensure_group(self, group: str) -> None:
        """Create the consumer group if absent (idempotent)."""
        ...

    async def read(
        self,
        group: str,
        consumer: str,
        *,
        count: int = 16,
        block_ms: int = 1000,
    ) -> list[StreamRecord]:
        """Claim up to ``count`` undelivered records for ``consumer``."""
        ...

    async def ack(self, group: str, *record_ids: str) -> int:
        """Mark records handled so they stop being pending."""
        ...

    async def reclaim(
        self,
        group: str,
        consumer: str,
        *,
        min_idle_ms: int = 60_000,
        count: int = 16,
    ) -> list[StreamRecord]:
        """Take over records another consumer claimed and never acknowledged."""
        ...


@dataclass
class _Pending:
    """A record handed to a consumer and not yet acknowledged."""

    record: StreamRecord
    consumer: str
    claimed_at: float
    deliveries: int = 1


class InMemoryEventStream:
    """Process-local stream implementing the full consumer-group contract.

    It is the development and test substrate, and the honest fallback when
    Redis is not configured: pending-entry tracking, reclaim and redelivery all
    behave as they do in Redis, so a consumer written against it is correct.
    What it cannot do is survive the process — for that, use
    :class:`~core.events.stream_redis.RedisEventStream`.
    """

    def __init__(self, maxlen: int = DEFAULT_STREAM_MAXLEN) -> None:
        self._records: deque[StreamRecord] = deque(maxlen=max(1, maxlen))
        self._cursors: dict[str, int] = {}
        self._pending: dict[str, OrderedDict[str, _Pending]] = {}
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._arrival: asyncio.Event = asyncio.Event()

    def _next_id(self) -> str:
        """Monotonic ``<millis>-<sequence>`` id, matching Redis' shape."""
        self._sequence += 1
        return f"{int(time.time() * 1000)}-{self._sequence}"

    async def append(self, event: Event, *, tenant_id: str = "default") -> str:
        """Write ``event`` to the log and return its record id."""
        async with self._lock:
            record = StreamRecord(id=self._next_id(), event=event, tenant_id=tenant_id)
            self._records.append(record)
        self._arrival.set()
        return record.id

    async def ensure_group(self, group: str) -> None:
        """Create the consumer group if absent (idempotent).

        A new group starts at the *end* of the log, like ``XGROUP CREATE ... $``
        — a consumer coming up must not replay every event the deployment has
        ever emitted.
        """
        async with self._lock:
            self._cursors.setdefault(group, self._sequence)
            self._pending.setdefault(group, OrderedDict())

    async def read(
        self,
        group: str,
        consumer: str,
        *,
        count: int = 16,
        block_ms: int = 1000,
    ) -> list[StreamRecord]:
        """Claim up to ``count`` undelivered records for ``consumer``."""
        await self.ensure_group(group)
        claimed = await self._claim_new(group, consumer, count)
        if claimed or block_ms <= 0:
            return claimed
        self._arrival.clear()
        try:
            await asyncio.wait_for(self._arrival.wait(), timeout=block_ms / 1000)
        except TimeoutError:
            return []
        return await self._claim_new(group, consumer, count)

    async def _claim_new(
        self, group: str, consumer: str, count: int
    ) -> list[StreamRecord]:
        async with self._lock:
            cursor = self._cursors.get(group, 0)
            fresh = [
                record for record in self._records if self._seq_of(record.id) > cursor
            ][:count]
            if not fresh:
                return []
            self._cursors[group] = self._seq_of(fresh[-1].id)
            pending = self._pending.setdefault(group, OrderedDict())
            now = time.time()
            for record in fresh:
                pending[record.id] = _Pending(record, consumer, now)
            return fresh

    @staticmethod
    def _seq_of(record_id: str) -> int:
        return int(record_id.rsplit("-", 1)[-1])

    async def ack(self, group: str, *record_ids: str) -> int:
        """Mark records handled so they stop being pending."""
        async with self._lock:
            pending = self._pending.get(group)
            if pending is None:
                return 0
            return sum(1 for rid in record_ids if pending.pop(rid, None) is not None)

    async def reclaim(
        self,
        group: str,
        consumer: str,
        *,
        min_idle_ms: int = 60_000,
        count: int = 16,
    ) -> list[StreamRecord]:
        """Take over records another consumer claimed and never acknowledged."""
        cutoff = time.time() - min_idle_ms / 1000
        async with self._lock:
            pending = self._pending.get(group)
            if not pending:
                return []
            taken: list[StreamRecord] = []
            for entry in list(pending.values()):
                if len(taken) >= count or entry.claimed_at > cutoff:
                    continue
                entry.consumer = consumer
                entry.claimed_at = time.time()
                entry.deliveries += 1
                taken.append(
                    StreamRecord(
                        id=entry.record.id,
                        event=entry.record.event,
                        tenant_id=entry.record.tenant_id,
                        delivery_count=entry.deliveries,
                    )
                )
            return taken

    def pending_count(self, group: str) -> int:
        """How many records this group has claimed and not acknowledged."""
        return len(self._pending.get(group, ()))


@dataclass
class StreamStats:
    """Counters a consumer reports for observability."""

    consumed: int = 0
    acknowledged: int = 0
    failed: int = 0
    reclaimed: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert the counters to a dictionary for reporting."""
        return {
            "consumed": self.consumed,
            "acknowledged": self.acknowledged,
            "failed": self.failed,
            "reclaimed": self.reclaimed,
            **self.extra,
        }
