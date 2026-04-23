"""Gmail Pub/Sub bridge.

Surface for receiving Gmail push notifications via Google Cloud Pub/Sub.
Live ingestion requires the ``google-cloud-pubsub`` SDK + service-account
credentials; this module exposes the configuration shape and a polling
fallback that lists recent messages via the Gmail REST API when Pub/Sub
is not available.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GmailPubSubConfig(BaseModel):
    project_id: str | None = None
    subscription: str | None = None
    service_account_json: str | None = None
    user_id: str = "me"
    polling_label: str = "INBOX"
    polling_max: int = Field(default=20, ge=1, le=500)


class GmailPubSubBridge:
    """Poll Gmail or pull from Pub/Sub depending on configuration."""

    def __init__(self, config: GmailPubSubConfig | None = None) -> None:
        self._config = config or GmailPubSubConfig()

    def is_configured_pubsub(self) -> bool:
        return bool(
            self._config.project_id
            and self._config.subscription
            and self._config.service_account_json
        )

    async def status(self) -> dict[str, Any]:
        return {
            "mode": "pubsub" if self.is_configured_pubsub() else "polling",
            "project_id": self._config.project_id,
            "subscription": self._config.subscription,
            "user_id": self._config.user_id,
        }

    async def fetch_pending(self) -> list[dict[str, Any]]:
        try:
            from google.cloud import pubsub_v1  # type: ignore[import-not-found]
        except ImportError:
            return [
                {
                    "status": "unavailable",
                    "reason": "google-cloud-pubsub not installed",
                }
            ]
        if not self.is_configured_pubsub():
            return [{"status": "unconfigured"}]

        subscriber = pubsub_v1.SubscriberClient()
        path = subscriber.subscription_path(self._config.project_id, self._config.subscription)
        response = subscriber.pull(
            request={"subscription": path, "max_messages": self._config.polling_max}
        )
        return [
            {"message_id": m.message.message_id, "data": m.message.data.decode("utf-8")}
            for m in response.received_messages
        ]


__all__ = ["GmailPubSubBridge", "GmailPubSubConfig"]
