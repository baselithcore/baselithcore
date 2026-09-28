"""The connector registry and the factory that builds configured instances.

Plugins contribute connectors through ``Plugin.get_connectors()``; the
plugin registry records them here with the plugin as owner, so unloading a
plugin withdraws exactly its connectors. Consumers never construct a
connector by hand: :func:`create_connector` resolves its credentials for the
caller's tenant first.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.connectors.credentials import CredentialResolver, get_credential_resolver
from core.connectors.errors import ConnectorConfigError
from core.connectors.protocols import Connector
from core.connectors.types import ConnectorCapability, ConnectorSpec
from core.registries import BaseRegistry

ConnectorFactory = Callable[..., Connector]


@dataclass(frozen=True, slots=True)
class ConnectorEntry:
    """A registered connector: its spec, how to build it, who contributed it.

    Attributes:
        spec: The connector's declared surface.
        factory: Called as ``factory(credentials=..., **kwargs)``. A
            ``BaseConnector`` subclass is its own factory.
        owner: Name of the contributing plugin, if any.
    """

    spec: ConnectorSpec
    factory: ConnectorFactory
    owner: str | None = None


class ConnectorRegistry(BaseRegistry[ConnectorEntry]):
    """Name-keyed registry of :class:`ConnectorEntry` objects."""

    def __init__(self) -> None:
        super().__init__(key=lambda entry: entry.spec.name)

    def register_connector(
        self, item: Any, *, owner: str | None = None, overwrite: bool = False
    ) -> ConnectorEntry:
        """Register a connector class/factory with a ``spec``, or an entry.

        Raises:
            TypeError: ``item`` carries no ``ConnectorSpec``.
            DuplicateRegistrationError: The name is taken and ``overwrite``
                is False.
        """
        if isinstance(item, ConnectorEntry):
            entry = (
                item
                if owner is None
                else ConnectorEntry(item.spec, item.factory, owner)
            )
        else:
            spec = getattr(item, "spec", None)
            if not isinstance(spec, ConnectorSpec) or not callable(item):
                raise TypeError(
                    f"{item!r} is not a connector: expected a ConnectorEntry or a "
                    "callable with a 'spec: ConnectorSpec' attribute"
                )
            entry = ConnectorEntry(spec=spec, factory=item, owner=owner)
        self.register(entry, overwrite=overwrite)
        return entry

    def remove_owned_by(self, owner: str) -> builtins.list[str]:
        """Withdraw every connector ``owner`` contributed; return their names."""
        with self._lock:
            names = [n for n, e in self._items.items() if e.owner == owner]
            for name in names:
                del self._items[name]
        return names

    def specs(self) -> builtins.list[ConnectorSpec]:
        """Specs of every registered connector."""
        return [entry.spec for entry in self.list()]

    def with_capability(
        self, capability: ConnectorCapability
    ) -> builtins.list[ConnectorEntry]:
        """Entries whose spec declares ``capability``."""
        return self.list(lambda entry: capability in entry.spec.capabilities)


_registry: ConnectorRegistry | None = None


def get_connector_registry() -> ConnectorRegistry:
    """The process-wide connector registry."""
    global _registry
    if _registry is None:
        _registry = ConnectorRegistry()
    return _registry


def reset_connector_registry() -> None:
    """Drop the process-wide registry (tests)."""
    global _registry
    _registry = None


async def create_connector(
    name: str,
    *,
    tenant_id: str | None = None,
    resolver: CredentialResolver | None = None,
    registry: ConnectorRegistry | None = None,
    **kwargs: Any,
) -> Connector:
    """Build connector ``name`` with credentials resolved for a tenant.

    Args:
        name: Registered connector name.
        tenant_id: Tenant to resolve credentials for; defaults to the tenant
            bound in the current context.
        resolver: Credential resolver; defaults to the process-wide one.
        registry: Registry to look in; defaults to the process-wide one.
        **kwargs: Forwarded to the factory (e.g. ``client=``).

    Raises:
        ConnectorConfigError: No connector is registered under ``name``.
    """
    entry = (registry or get_connector_registry()).get(name)
    if entry is None:
        raise ConnectorConfigError(name, f"no connector registered as {name!r}")
    if tenant_id is None:
        from core.context import get_tenant_or_default

        tenant_id = get_tenant_or_default()
    credentials = await (resolver or get_credential_resolver()).resolve(
        entry.spec, tenant_id=tenant_id
    )
    return entry.factory(credentials=credentials, **kwargs)


__all__ = [
    "ConnectorEntry",
    "ConnectorFactory",
    "ConnectorRegistry",
    "create_connector",
    "get_connector_registry",
    "reset_connector_registry",
]
