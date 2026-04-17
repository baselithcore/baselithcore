"""Abstract base for all multi-channel adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ChannelStatus(str, Enum):
    """Lifecycle state for a single channel adapter."""

    UNCONFIGURED = "unconfigured"
    READY = "ready"
    SENDING = "sending"
    ERROR = "error"


class ChannelMessage(BaseModel):
    """Outbound message envelope routed through a channel adapter."""

    channel: str
    target: str = Field(..., description="Channel-specific recipient identifier.")
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChannelAdapter(ABC):
    """Abstract interface every channel adapter must implement."""

    name: str = "abstract"
    requires_credentials: tuple[str, ...] = ()

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._status = ChannelStatus.UNCONFIGURED

    @property
    def config(self) -> dict[str, Any]:
        return dict(self._config)

    @property
    def status(self) -> ChannelStatus:
        return self._status

    def is_configured(self) -> bool:
        """Return True if all required credentials are present."""
        return all(self._config.get(key) for key in self.requires_credentials)

    async def startup(self) -> None:
        self._status = (
            ChannelStatus.READY if self.is_configured() else ChannelStatus.UNCONFIGURED
        )

    async def shutdown(self) -> None:
        self._status = ChannelStatus.UNCONFIGURED

    @abstractmethod
    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        """Deliver an outbound message via the underlying transport."""


__all__ = ["ChannelAdapter", "ChannelMessage", "ChannelStatus"]
