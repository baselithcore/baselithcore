"""
Domain types for the webhook subsystem.

An *endpoint* is a subscription (a URL + secret + the event types it wants). An
*event* is something that happened in the framework. A *delivery* is one attempt
(and its outcome) to POST an event to an endpoint.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, Optional, Set

from pydantic import BaseModel, ConfigDict, Field, SecretStr

# Wildcard event subscription — an endpoint with this in its event_types
# receives every event.
WILDCARD_EVENT = "*"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class WebhookEndpoint(BaseModel):
    """A registered webhook subscription."""

    id: str = Field(default_factory=lambda: _new_id("whe"))
    tenant_id: str = "default"
    url: str
    # HMAC signing secret. Wrapped so it never leaks via repr/logs/Sentry.
    secret: SecretStr
    # Event types this endpoint subscribes to; ``{"*"}`` means all.
    event_types: Set[str] = Field(default_factory=lambda: {WILDCARD_EVENT})
    enabled: bool = True
    description: Optional[str] = None
    # Extra static headers sent with every delivery (e.g. a routing token).
    headers: Dict[str, str] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)

    model_config = ConfigDict(extra="forbid")

    def subscribes_to(self, event_type: str) -> bool:
        """Whether this endpoint should receive ``event_type``."""
        return WILDCARD_EVENT in self.event_types or event_type in self.event_types

    def redacted(self) -> Dict[str, Any]:
        """Serializable view with the secret removed (safe for API responses)."""
        data = self.model_dump(exclude={"secret"})
        data["has_secret"] = True
        return data


class WebhookEvent(BaseModel):
    """An event emitted by the framework, delivered to subscribers."""

    id: str = Field(default_factory=lambda: _new_id("evt"))
    type: str
    tenant_id: str = "default"
    created_at: float = Field(default_factory=time.time)
    data: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    def envelope(self) -> Dict[str, Any]:
        """The JSON body delivered to subscribers."""
        return {
            "id": self.id,
            "type": self.type,
            "created_at": self.created_at,
            "tenant_id": self.tenant_id,
            "data": self.data,
        }


class DeliveryStatus(str, Enum):
    """Lifecycle state of a webhook delivery."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class WebhookDelivery(BaseModel):
    """The record of attempting to deliver an event to an endpoint."""

    id: str = Field(default_factory=lambda: _new_id("whd"))
    endpoint_id: str
    event_id: str
    event_type: str
    tenant_id: str = "default"
    url: str
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempts: int = 0
    last_status_code: Optional[int] = None
    last_error: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    completed_at: Optional[float] = None
    # The serialized event envelope, retained so a failed delivery can be replayed.
    payload: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")
