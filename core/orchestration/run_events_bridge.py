"""Redis bridge for cross-replica run-event delivery.

The local :class:`~core.orchestration.run_events.RunEventStream` fans out per
process only: behind the default 2+-replica HPA, an SSE client on
``GET /runs/{id}/events`` sees events only when its connection lands on the
replica executing the run. This bridge closes that gap:

* **Publish**: installed as the stream's broadcaster, every
  ``publish_run_event`` is serialized and published to the Redis channel
  ``events:run:<run_id>`` (fire-and-forget task off the running loop).
* **Listen**: one pattern subscription per process re-injects every received
  event into the local stream — including on the publishing replica itself
  (one Redis round trip of latency buys symmetry with no dedup machinery).

Failure policy is fail-open at both ends: a broken publish falls back to
local fan-out (see ``run_events.publish_run_event``); a dropped listener
connection is retried with backoff. Opt-in via
``BASELITH_RUN_EVENTS_BRIDGE=redis`` (wired in the app lifespan).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from core.api.events import AgentEvent
from core.observability.logging import get_logger

logger = get_logger(__name__)

RUN_EVENTS_CHANNEL_PREFIX = "events:run:"

#: Backoff between listener reconnect attempts.
_RECONNECT_DELAY_SECONDS = 2.0


class RedisRunEventsBridge:
    """Cross-replica run-event fan-out over Redis pub/sub."""

    def __init__(
        self,
        publisher: Any | None = None,
        subscriber: Any | None = None,
        redis_url: str | None = None,
    ) -> None:
        """
        Args:
            publisher: Async Redis client used to publish (tests/DI).
            subscriber: Async Redis client used to listen (tests/DI). The
                subscriber holds its connection for the listener's lifetime,
                so it must be distinct from short-lived pooled clients.
            redis_url: Connection URL used to build missing clients lazily;
                defaults to the configured cache Redis.
        """
        self._publisher = publisher
        self._subscriber = subscriber
        self._redis_url = redis_url
        self._listener_task: asyncio.Task[None] | None = None
        # Fire-and-forget publish tasks need a strong reference until done.
        self._publish_tasks: set[asyncio.Task[None]] = set()

    def _build_client(self) -> Any:
        from core.cache.redis_cache import create_redis_client
        from core.config import get_redis_cache_config

        url = self._redis_url or get_redis_cache_config().url
        return create_redis_client(url, decode_responses=True)

    async def start(self) -> None:
        """Install the broadcaster and start the listener task."""
        from core.orchestration.run_events import set_run_event_broadcaster

        if self._publisher is None:
            self._publisher = self._build_client()
        if self._subscriber is None:
            self._subscriber = self._build_client()
        self._listener_task = asyncio.get_running_loop().create_task(
            self._listen(), name="run-events-bridge-listener"
        )
        set_run_event_broadcaster(self.broadcast)
        logger.info("run_events_bridge_started")

    async def stop(self) -> None:
        """Uninstall the broadcaster and tear the listener down (idempotent)."""
        from core.orchestration.run_events import set_run_event_broadcaster

        set_run_event_broadcaster(None)
        task, self._listener_task = self._listener_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for pending in list(self._publish_tasks):
            pending.cancel()
        self._publish_tasks.clear()
        logger.info("run_events_bridge_stopped")

    # -- publish side ------------------------------------------------------

    def broadcast(self, run_id: str, event: AgentEvent) -> None:
        """Broadcaster hook: schedule the Redis publish off the running loop.

        Synchronous by contract (``publish_run_event`` is sync); raises when
        no loop is running, which the caller treats as a fallback to local
        fan-out.
        """
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._publish(run_id, event))
        self._publish_tasks.add(task)
        task.add_done_callback(self._publish_tasks.discard)

    async def _publish(self, run_id: str, event: AgentEvent) -> None:
        publisher = self._publisher
        if publisher is None:  # stopped between schedule and execution
            return
        try:
            await publisher.publish(
                f"{RUN_EVENTS_CHANNEL_PREFIX}{run_id}", event.model_dump_json()
            )
        except Exception as exc:
            # The event was not delivered locally either (broadcast owns
            # delivery) — re-inject locally so this replica's subscribers
            # still see it.
            logger.warning(
                "run_events_bridge_publish_failed_local_reinject",
                extra={"run_id": run_id, "error": str(exc)},
            )
            from core.orchestration.run_events import get_run_event_stream

            get_run_event_stream().publish(run_id, event)

    # -- listen side -------------------------------------------------------

    async def _listen(self) -> None:
        """Re-inject every bridged event into the local stream, forever.

        Reconnects with backoff on connection loss; cancelled on stop.
        """
        from core.orchestration.run_events import get_run_event_stream

        while True:
            subscriber = self._subscriber
            if subscriber is None:  # stopped
                return
            try:
                pubsub = subscriber.pubsub()
                await pubsub.psubscribe(f"{RUN_EVENTS_CHANNEL_PREFIX}*")
                async for message in pubsub.listen():
                    if message.get("type") != "pmessage":
                        continue
                    channel = str(message.get("channel", ""))
                    run_id = channel.removeprefix(RUN_EVENTS_CHANNEL_PREFIX)
                    if not run_id:
                        continue
                    try:
                        event = AgentEvent.model_validate_json(message["data"])
                    except Exception as exc:
                        logger.warning(
                            "run_events_bridge_malformed_event",
                            extra={"channel": channel, "error": str(exc)},
                        )
                        continue
                    get_run_event_stream().publish(run_id, event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "run_events_bridge_listener_reconnect",
                    extra={"error": str(exc)},
                )
                await asyncio.sleep(_RECONNECT_DELAY_SECONDS)


__all__ = ["RUN_EVENTS_CHANNEL_PREFIX", "RedisRunEventsBridge"]
