"""Redis Streams backend for :class:`~core.events.stream.EventStream`.

Redis Streams is the smallest thing that satisfies the contract without adding
infrastructure a deployment does not already run: an append-only log with
server-side consumer groups, pending-entry tracking and ``XAUTOCLAIM`` for
records a dead consumer never acknowledged. Kafka would do the same at a scale
this runtime does not assume, and a Postgres table would do it with a poller
this avoids.

    stream = RedisEventStream(client, key="baselith:events")
    await stream.ensure_group("projections")
    for record in await stream.read("projections", consumer="pod-7"):
        await handle(record.event)
        await stream.ack("projections", record.id)

Two Redis behaviours are load-bearing and easy to get wrong:

* ``XGROUP CREATE`` raises ``BUSYGROUP`` when the group exists. That is the
  *success* case for an idempotent ``ensure_group`` and is swallowed; every
  other error propagates.
* ``XADD ... MAXLEN ~ n`` trims approximately, at radix-tree node boundaries.
  Exact trimming (``MAXLEN n``) walks the log on every append. The bound is a
  redelivery buffer, so approximate is right and the ``~`` is deliberate.

The client is injected rather than resolved from configuration: the cache layer
already owns Redis connection handling, and a stream that opened its own
connection would double the pool a deployment has to size.
"""

from __future__ import annotations

from typing import Any

from core.events.stream import (
    DEFAULT_STREAM_MAXLEN,
    StreamRecord,
    decode_event_fields,
    encode_event_fields,
)
from core.events.types import Event
from core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = ["DEFAULT_STREAM_KEY", "RedisEventStream"]

#: Default stream key. One stream carries every event name: consumer groups,
#: not separate keys, are how consumers divide the work — a key per event name
#: would multiply the groups an operator has to reason about and lose the
#: total order that makes replay meaningful.
DEFAULT_STREAM_KEY = "baselith:events"


def _text(value: Any) -> str:
    """Decode a Redis reply that may be ``bytes`` depending on the client."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _fields(raw: dict[Any, Any]) -> dict[str, str]:
    """Normalise an entry's field map to ``str -> str``."""
    return {_text(k): _text(v) for k, v in raw.items()}


class RedisEventStream:
    """Durable, cross-replica event log backed by a Redis stream.

    Args:
        client: An ``redis.asyncio.Redis`` instance. Injected — see the module
            docstring.
        key: Stream key.
        maxlen: Approximate cap on retained records.
    """

    def __init__(
        self,
        client: Any,
        *,
        key: str = DEFAULT_STREAM_KEY,
        maxlen: int = DEFAULT_STREAM_MAXLEN,
    ) -> None:
        self._client = client
        self._key = key
        self._maxlen = max(1, maxlen)

    @property
    def key(self) -> str:
        """The Redis key holding this stream."""
        return self._key

    async def append(self, event: Event, *, tenant_id: str = "default") -> str:
        """Write ``event`` to the log and return its record id."""
        record_id = await self._client.xadd(
            self._key,
            encode_event_fields(event, tenant_id),
            maxlen=self._maxlen,
            approximate=True,
        )
        return _text(record_id)

    async def ensure_group(self, group: str) -> None:
        """Create the consumer group if absent (idempotent).

        ``mkstream=True`` creates the stream key along with the group, so a
        consumer may start before the first event is ever published. The group
        starts at ``$`` — the end of the log — because a replica coming up must
        not replay the deployment's entire history.
        """
        try:
            await self._client.xgroup_create(self._key, group, id="$", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
            logger.debug(f"consumer group '{group}' already exists")

    async def read(
        self,
        group: str,
        consumer: str,
        *,
        count: int = 16,
        block_ms: int = 1000,
    ) -> list[StreamRecord]:
        """Claim up to ``count`` undelivered records for ``consumer``.

        ``>`` asks only for records no member of the group has been given yet;
        records this consumer already holds are recovered through
        :meth:`reclaim`, not here.
        """
        response = await self._client.xreadgroup(
            group,
            consumer,
            {self._key: ">"},
            count=count,
            block=block_ms if block_ms > 0 else None,
        )
        if not response:
            return []
        records: list[StreamRecord] = []
        for _stream_key, entries in response:
            records.extend(self._to_records(entries))
        return records

    async def ack(self, group: str, *record_ids: str) -> int:
        """Mark records handled so they stop being pending."""
        if not record_ids:
            return 0
        return int(await self._client.xack(self._key, group, *record_ids))

    async def reclaim(
        self,
        group: str,
        consumer: str,
        *,
        min_idle_ms: int = 60_000,
        count: int = 16,
    ) -> list[StreamRecord]:
        """Take over records another consumer claimed and never acknowledged.

        This is the crash-recovery path: a pod killed between handling and
        acknowledging leaves its entries pending forever, and nothing else in
        Redis reassigns them.

        Args:
            group: Consumer group.
            consumer: The member taking ownership.
            min_idle_ms: Only entries idle at least this long are taken — set
                it comfortably above the slowest legitimate handler, or a live
                consumer's work is stolen mid-flight.
            count: Maximum entries to reclaim per call.

        Returns:
            The reclaimed records, whose ``delivery_count`` is greater than one.
        """
        response = await self._client.xautoclaim(
            self._key,
            group,
            consumer,
            min_idle_time=min_idle_ms,
            count=count,
        )
        # XAUTOCLAIM replies (next_cursor, entries[, deleted]) — the third
        # element only exists from Redis 7. Index from the front, never assume
        # the length.
        entries = response[1] if len(response) > 1 else []
        records = self._to_records(entries, redelivered=True)
        # Entries deleted from the stream still occupy the pending list and
        # would be re-claimed forever. There is nothing to hand a handler, so
        # acknowledge them here rather than leaking a growing backlog.
        tombstones = [_text(entry[0]) for entry in entries or () if entry[1] is None]
        if tombstones:
            await self.ack(group, *tombstones)
            logger.warning(f"reclaimed {len(tombstones)} deleted stream entries")
        return records

    async def pending_count(self, group: str) -> int:
        """How many records this group has claimed and not acknowledged."""
        summary = await self._client.xpending(self._key, group)
        if isinstance(summary, dict):
            return int(summary.get("pending", 0))
        return int(summary[0]) if summary else 0

    def _to_records(
        self, entries: Any, *, redelivered: bool = False
    ) -> list[StreamRecord]:
        """Turn raw stream entries into records, skipping tombstones."""
        records: list[StreamRecord] = []
        for entry in entries or ():
            record_id, raw = entry
            if raw is None:
                # XAUTOCLAIM reports an entry deleted from the stream as an
                # (id, None) pair. The caller acknowledges those; there is no
                # event to deliver.
                continue
            event, tenant_id = decode_event_fields(_fields(raw))
            records.append(
                StreamRecord(
                    id=_text(record_id),
                    event=event,
                    tenant_id=tenant_id,
                    delivery_count=2 if redelivered else 1,
                )
            )
        return records
