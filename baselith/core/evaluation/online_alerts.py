"""Route online-evaluation quality transitions to operators.

A :class:`~.online_monitor.MonitorTransition` (a series degrading or
recovering) is announced on every channel the framework already has for it:

* a structured log line (``online_eval_degraded`` at warning,
  ``online_eval_recovered`` at info);
* the in-process event bus (``EVALUATION_DEGRADED`` / ``EVALUATION_RECOVERED``),
  for subscribers inside the process that announced it;
* outbound webhooks (event types ``evaluation.degraded`` and
  ``evaluation.recovered``) to the endpoints of ``EVAL_ONLINE_WEBHOOK_TENANT``
  that subscribe to them, when ``WEBHOOKS_ENABLED`` is on.

The webhook delivery (with its retries) runs as a background task, so a slow
or failing subscriber never holds up the evaluation worker or the fleet tick
that announced the transition; a failed emission counts as
``mas_eval_online_failures_total{stage="webhook"}``.

The ``mas_eval_online_degraded`` gauge is not set here: it belongs to whoever
holds the series state (the per-process monitor, or every worker mirroring the
fleet state), and the Prometheus alert rules shipped in ``deploy/`` page on it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from baselith.core.events import EventBus, EventNames
from baselith.core.observability.logging import get_logger

from .online_metrics import record_failure
from .online_monitor import DEGRADED, MonitorTransition

logger = get_logger(__name__)

#: Webhook event types an endpoint subscribes to for quality transitions.
WEBHOOK_EVENT_TYPES: tuple[str, str] = (
    EventNames.EVALUATION_DEGRADED,
    EventNames.EVALUATION_RECOVERED,
)
#: ``scope`` of a transition decided over the whole fleet.
SCOPE_FLEET = "fleet"
#: ``scope`` of a transition decided by one worker over its own traffic.
SCOPE_WORKER = "worker"


class TransitionAnnouncer:
    """Announce quality transitions on the log, the event bus and webhooks.

    Args:
        event_bus: Bus the ``EVALUATION_*`` events are emitted on.
        webhook_tenant: Tenant whose webhook endpoints receive the events.
    """

    def __init__(self, event_bus: EventBus, *, webhook_tenant: str = "default") -> None:
        self.event_bus = event_bus
        self.webhook_tenant = webhook_tenant
        self._pending: set[asyncio.Task[None]] = set()

    async def announce(self, transition: MonitorTransition, scope: str) -> None:
        """Log, emit and deliver one transition.

        Args:
            transition: The state change.
            scope: :data:`SCOPE_FLEET` or :data:`SCOPE_WORKER`.
        """
        payload: dict[str, Any] = {**transition.to_event(), "scope": scope}
        if transition.state == DEGRADED:
            event = EventNames.EVALUATION_DEGRADED
            logger.warning("online_eval_degraded", extra=payload)
        else:
            event = EventNames.EVALUATION_RECOVERED
            logger.info("online_eval_recovered", extra=payload)
        await self.event_bus.emit(event, payload)
        self._deliver_webhook(event, payload)

    def _deliver_webhook(self, event: str, payload: dict[str, Any]) -> None:
        from baselith.core.config.webhooks import get_webhook_config

        if not get_webhook_config().enabled:
            return
        task = asyncio.create_task(
            self._emit_webhook(event, payload), name="online-eval-webhook"
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _emit_webhook(self, event: str, payload: dict[str, Any]) -> None:
        try:
            from baselith.core.webhooks.service import get_webhook_service

            await get_webhook_service().emit(
                event, dict(payload), tenant_id=self.webhook_tenant
            )
        except Exception as exc:
            record_failure("webhook")
            logger.warning("online_eval_webhook_failed: %s", exc)

    async def drain(self) -> None:
        """Wait for the webhook deliveries still in flight."""
        if self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)


__all__ = [
    "SCOPE_FLEET",
    "SCOPE_WORKER",
    "WEBHOOK_EVENT_TYPES",
    "TransitionAnnouncer",
]
