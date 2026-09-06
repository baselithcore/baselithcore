"""Bridge between an :class:`~core.events.stream.EventStream` and the local bus.

The in-process :class:`~core.events.bus.EventBus` stays exactly what it is —
the fast path for handlers registered here. The bridge adds the second half a
multi-replica deployment needs:

* :meth:`DurableEventBridge.publish` writes the event to the durable log *and*
  emits it locally, so a crash cannot lose it and another replica can see it;
* :meth:`DurableEventBridge.run` consumes the log through a consumer group and
  re-emits each record on this process's bus, then acknowledges it.

Consumption never feeds back into the log: :meth:`~DurableEventBridge._dispatch`
emits on the bus directly rather than through :meth:`publish`, so a record read
from the stream is not appended again. A handler *may* publish through the
bridge — an event derived from the one it just handled belongs in the log like
any other, and suppressing it during dispatch would silently lose it.

    bridge = DurableEventBridge(stream, bus=get_event_bus(), group="projections")
    await bridge.start()          # background consumer
    await bridge.publish("order.paid", {"order_id": 7})
    ...
    await bridge.stop()

Delivery is at-least-once: a record is acknowledged *after* its handlers run,
so a process that dies in between sees it again. Handlers must therefore be
idempotent — for tool calls, that is what
:mod:`core.orchestration.idempotency` provides.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.context import reset_tenant_context, set_tenant_context
from core.events.bus import EventBus
from core.events.stream import EventStream, StreamRecord, StreamStats
from core.events.types import Event
from core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "DEFAULT_CONSUMER_GROUP",
    "DurableEventBridge",
    "POISON_DELIVERY_THRESHOLD",
]

DEFAULT_CONSUMER_GROUP = "baselith-core"

#: Redeliveries after which a record is acknowledged and dropped instead of
#: being handed round the group forever. A record that has failed this many
#: times is not going to succeed on the next attempt, and a permanently stuck
#: entry blocks the reclaim path for everything behind it.
POISON_DELIVERY_THRESHOLD = 5


class DurableEventBridge:
    """Publishes to a durable stream and replays it onto a local event bus.

    Args:
        stream: The durable log.
        bus: The local bus events are emitted on. Defaults to the process bus.
        group: Consumer group name. Every replica of one deployment shares a
            group — that is what makes each record handled once rather than
            once *per replica*.
        consumer: This member's name within the group; defaults to a name
            derived from the process id.
        block_ms: How long a read waits for new records before looping.
        reclaim_idle_ms: Records idle longer than this are taken from the
            consumer that never acknowledged them.
    """

    def __init__(
        self,
        stream: EventStream,
        *,
        bus: EventBus | None = None,
        group: str = DEFAULT_CONSUMER_GROUP,
        consumer: str | None = None,
        block_ms: int = 1000,
        reclaim_idle_ms: int = 60_000,
    ) -> None:
        import os

        self._stream = stream
        self._bus = bus
        self._group = group
        self._consumer = consumer or f"consumer-{os.getpid()}"
        self._block_ms = block_ms
        self._reclaim_idle_ms = reclaim_idle_ms
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self.stats = StreamStats()

    def _resolve_bus(self) -> EventBus:
        if self._bus is not None:
            return self._bus
        from core.events.bus import get_event_bus

        self._bus = get_event_bus()
        return self._bus

    # -- publish -----------------------------------------------------------

    async def publish(
        self,
        event_name: str,
        data: dict[str, Any] | None = None,
        *,
        source: str | None = None,
        correlation_id: str | None = None,
    ) -> str:
        """Record the event durably, then emit it on the local bus.

        The append happens **first**: an event emitted locally and lost before
        it reached the log is exactly the failure this bridge exists to remove.

        Safe to call from a handler running on a consumed record — the derived
        event is appended like any other, and nothing loops back, because
        consumption emits on the bus rather than republishing.

        Returns:
            The stream record id.
        """
        from core.context import get_tenant_or_default

        event = Event(
            name=event_name,
            data=data or {},
            source=source,
            correlation_id=correlation_id,
        )
        record_id = await self._stream.append(event, tenant_id=get_tenant_or_default())
        await self._resolve_bus().emit(
            event_name,
            event.data,
            source=source,
            correlation_id=correlation_id,
        )
        return record_id

    # -- consume -----------------------------------------------------------

    async def start(self) -> None:
        """Create the consumer group and start the background consumer."""
        if self._task is not None and not self._task.done():
            return
        await self._stream.ensure_group(self._group)
        self._stopping.clear()
        self._task = asyncio.create_task(self.run(), name="event-stream-consumer")
        logger.info(
            f"durable event consumer '{self._consumer}' joined group '{self._group}'"
        )

    async def stop(self, timeout: float = 5.0) -> None:
        """Ask the consumer to finish the current batch and stop.

        In-flight records are left unacknowledged rather than dropped: the
        group hands them to another member (or to this one on restart) via the
        reclaim path, which is the whole point of pending entries.
        """
        self._stopping.set()
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except TimeoutError:
            task.cancel()
            logger.warning("durable event consumer did not stop in time; cancelled")
        except asyncio.CancelledError:  # pragma: no cover - shutdown race
            pass

    async def run(self) -> None:
        """Consume until :meth:`stop` is called.

        Errors from one iteration never end the loop: a consumer that exits on
        a transient Redis failure stops the deployment's event processing until
        someone notices, which is strictly worse than retrying.
        """
        while not self._stopping.is_set():
            try:
                records = await self._stream.reclaim(
                    self._group,
                    self._consumer,
                    min_idle_ms=self._reclaim_idle_ms,
                )
                if records:
                    self.stats.reclaimed += len(records)
                else:
                    records = await self._stream.read(
                        self._group, self._consumer, block_ms=self._block_ms
                    )
                for record in records:
                    await self._dispatch(record)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.stats.failed += 1
                logger.warning(f"durable event consumer iteration failed: {exc}")
                await asyncio.sleep(min(5.0, self._block_ms / 1000))

    async def _dispatch(self, record: StreamRecord) -> None:
        """Emit one record on the local bus, then acknowledge it.

        The tenant that emitted the event is restored first: a handler running
        on a different replica has no request context, and a projection that
        wrote to the wrong tenant would be a cross-tenant defect rather than a
        lost event.
        """
        if record.delivery_count > POISON_DELIVERY_THRESHOLD:
            logger.error(
                f"dropping event '{record.event.name}' after "
                f"{record.delivery_count} deliveries (record {record.id})"
            )
            await self._stream.ack(self._group, record.id)
            return

        token = set_tenant_context(record.tenant_id)
        try:
            await self._resolve_bus().emit(
                record.event.name,
                record.event.data,
                source=record.event.source,
                correlation_id=record.event.correlation_id,
            )
        except Exception as exc:
            # Not acknowledged: the record stays pending and comes back through
            # the reclaim path, up to the poison threshold.
            self.stats.failed += 1
            logger.warning(f"handling event '{record.event.name}' failed: {exc}")
            return
        finally:
            reset_tenant_context(token)

        self.stats.consumed += 1
        self.stats.acknowledged += await self._stream.ack(self._group, record.id)
