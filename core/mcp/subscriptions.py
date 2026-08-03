"""``subscriptions/listen``: the one long-lived stream in 2026-07-28.

The revision removed the standalone GET stream and ``resources/subscribe``.
Everything server-initiated now flows on the response stream of a
``subscriptions/listen`` request, and only the notification types the client
explicitly asked for: a server that pushed anything else would be sending a
message the client has no contract to interpret.

The subscription id is the JSON-RPC id of the listen request. On stdio all
subscriptions share one channel, so every message carries that id in ``_meta``
for the client to demultiplex.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

SUBSCRIPTION_ID_KEY = "io.modelcontextprotocol/subscriptionId"

TOOLS_LIST_CHANGED = "notifications/tools/list_changed"
PROMPTS_LIST_CHANGED = "notifications/prompts/list_changed"
RESOURCES_LIST_CHANGED = "notifications/resources/list_changed"
RESOURCES_UPDATED = "notifications/resources/updated"
ACKNOWLEDGED = "notifications/subscriptions/acknowledged"

# Filter field → the notification it enables.
LIST_FILTERS = {
    "toolsListChanged": TOOLS_LIST_CHANGED,
    "promptsListChanged": PROMPTS_LIST_CHANGED,
    "resourcesListChanged": RESOURCES_LIST_CHANGED,
}

Sender = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class Subscription:
    """One open listen stream and what it agreed to receive."""

    subscription_id: Any
    send: Sender
    list_changed: set[str] = field(default_factory=set)
    resource_uris: set[str] = field(default_factory=set)
    closed: asyncio.Event = field(default_factory=asyncio.Event)

    def wants(self, method: str, uri: str | None = None) -> bool:
        if method == RESOURCES_UPDATED:
            return uri in self.resource_uris
        return method in self.list_changed

    def acknowledged_filter(self) -> dict[str, Any]:
        """The subset actually honored, which is what the ack must report."""
        agreed: dict[str, Any] = {
            field_name: True
            for field_name, method in LIST_FILTERS.items()
            if method in self.list_changed
        }
        if self.resource_uris:
            agreed["resourceSubscriptions"] = sorted(self.resource_uris)
        return agreed


class SubscriptionHub:
    """Fan-out of change notifications to the streams that asked for them."""

    def __init__(self) -> None:
        self._subscriptions: dict[Any, Subscription] = {}

    def open(
        self, subscription_id: Any, send: Sender, notifications: dict[str, Any]
    ) -> Subscription:
        """Register a subscription for the requested notification types."""
        subscription = Subscription(
            subscription_id=subscription_id,
            send=send,
            list_changed={
                method
                for field_name, method in LIST_FILTERS.items()
                if notifications.get(field_name)
            },
            resource_uris=set(notifications.get("resourceSubscriptions") or []),
        )
        self._subscriptions[subscription_id] = subscription
        logger.info(
            "mcp_subscription_opened",
            subscription_id=subscription_id,
            filter=subscription.acknowledged_filter(),
        )
        return subscription

    def close(self, subscription_id: Any) -> None:
        subscription = self._subscriptions.pop(subscription_id, None)
        if subscription is not None:
            subscription.closed.set()
            logger.info("mcp_subscription_closed", subscription_id=subscription_id)

    def close_all(self) -> None:
        for subscription_id in list(self._subscriptions):
            self.close(subscription_id)

    @property
    def active(self) -> int:
        return len(self._subscriptions)

    def wants_any(self, method: str) -> bool:
        """Whether any open stream would receive *method* — cheap pre-check."""
        return any(s.wants(method) for s in self._subscriptions.values())

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Deliver a notification to every subscription that opted into it.

        A stream whose send fails is dropped rather than retried: the client is
        gone, and holding the subscription would leak it for the process's life.
        """
        uri = (params or {}).get("uri")
        for subscription in list(self._subscriptions.values()):
            if not subscription.wants(method, uri):
                continue
            message = {
                "jsonrpc": "2.0",
                "method": method,
                "params": {
                    **(params or {}),
                    "_meta": {SUBSCRIPTION_ID_KEY: subscription.subscription_id},
                },
            }
            try:
                await subscription.send(message)
            except Exception as exc:
                logger.info(
                    "mcp_subscription_send_failed",
                    subscription_id=subscription.subscription_id,
                    error=str(exc),
                )
                self.close(subscription.subscription_id)

    async def acknowledge(self, subscription: Subscription) -> None:
        """Send the mandatory first message of a subscription's stream."""
        await subscription.send(
            {
                "jsonrpc": "2.0",
                "method": ACKNOWLEDGED,
                "params": {
                    "_meta": {SUBSCRIPTION_ID_KEY: subscription.subscription_id},
                    "notifications": subscription.acknowledged_filter(),
                },
            }
        )


class SubscriptionHandlerMixin:
    """Serves ``subscriptions/listen`` and emits the change notifications."""

    _subscriptions: SubscriptionHub

    async def _handle_listen(
        self, params: dict[str, Any], subscription_id: Any, send: Sender
    ) -> dict[str, Any]:
        """Open a stream and hold it until the client or the server ends it.

        Returns the graceful-closure result: an empty result correlated to the
        listen request, which tells the client the stream ended on purpose
        rather than being dropped by the transport.
        """
        subscription = self._subscriptions.open(
            subscription_id, send, params.get("notifications") or {}
        )
        await self._subscriptions.acknowledge(subscription)
        try:
            await subscription.closed.wait()
        finally:
            self._subscriptions.close(subscription_id)
        return {"_meta": {SUBSCRIPTION_ID_KEY: subscription_id}}

    async def notify_tools_changed(self) -> None:
        """Announce that ``tools/list`` would now return something different."""
        await self._subscriptions.notify(TOOLS_LIST_CHANGED)

    async def notify_prompts_changed(self) -> None:
        await self._subscriptions.notify(PROMPTS_LIST_CHANGED)

    async def notify_resources_changed(self) -> None:
        await self._subscriptions.notify(RESOURCES_LIST_CHANGED)

    async def notify_resource_updated(self, uri: str) -> None:
        """Announce that the contents behind *uri* changed."""
        await self._subscriptions.notify(RESOURCES_UPDATED, {"uri": uri})


__all__ = [
    "ACKNOWLEDGED",
    "LIST_FILTERS",
    "PROMPTS_LIST_CHANGED",
    "RESOURCES_LIST_CHANGED",
    "RESOURCES_UPDATED",
    "SUBSCRIPTION_ID_KEY",
    "TOOLS_LIST_CHANGED",
    "Subscription",
    "SubscriptionHandlerMixin",
    "SubscriptionHub",
]
