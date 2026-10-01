"""The connector protocols: one base, one per capability.

They are ``runtime_checkable``, so a consumer tests for a capability with
``isinstance(connector, SupportsSync)`` instead of trusting a declared flag,
and a plugin can satisfy them without inheriting from
:class:`~core.connectors.base.BaseConnector` at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from core.connectors.types import (
    ActionResult,
    ConnectorHealth,
    ConnectorSpec,
    InboundEvent,
    SyncPage,
)


@runtime_checkable
class Connector(Protocol):
    """What every connector provides, whatever its capabilities."""

    spec: ConnectorSpec

    async def health(self) -> ConnectorHealth:
        """Report whether the external system is reachable and usable."""
        ...

    async def aclose(self) -> None:
        """Release network resources the connector owns."""
        ...


@runtime_checkable
class SupportsLookup(Protocol):
    """Keyed query returning one record or nothing (enrichment, search)."""

    async def lookup(self, key: str) -> Mapping[str, Any] | None:
        """Return the record for ``key``, or ``None`` when there is none."""
        ...


@runtime_checkable
class SupportsSync(Protocol):
    """Incremental, cursor-paged pull of records."""

    async def sync(
        self, cursor: str | None = None, *, limit: int | None = None
    ) -> SyncPage:
        """Return the page after ``cursor`` (the first page when ``None``)."""
        ...


@runtime_checkable
class SupportsAction(Protocol):
    """Side-effecting operations declared in ``spec.actions``."""

    async def invoke(self, action: str, params: Mapping[str, Any]) -> ActionResult:
        """Run ``action`` with ``params``; a business refusal is ``ok=False``."""
        ...


@runtime_checkable
class SupportsInbound(Protocol):
    """Events the external system pushes to us (webhooks)."""

    def verify(self, headers: Mapping[str, str], body: bytes) -> None:
        """Authenticate the delivery; raise ``ConnectorAuthError`` to refuse."""
        ...

    def parse(self, headers: Mapping[str, str], body: bytes) -> InboundEvent | None:
        """Normalise a verified delivery; ``None`` for events to ignore."""
        ...


__all__ = [
    "Connector",
    "SupportsAction",
    "SupportsInbound",
    "SupportsLookup",
    "SupportsSync",
]
