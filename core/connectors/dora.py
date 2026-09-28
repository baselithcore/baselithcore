"""Declare connector vendors into the DORA Register of Information.

Every connector that names a ``vendor`` is a contractual ICT dependency in
the sense of Regulation (EU) 2022/2554 Art. 28. :func:`declare_ict_providers`
seeds the register (:mod:`core.thirdparty`) with those providers so the
operator starts from what the deployment really talks to. It is explicit,
not a side effect of registration: the operator decides when to declare.

Provider ids are derived from the vendor name, so declaring again finds the
same row, and an existing row is left exactly as the operator edited it.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from core.connectors.registry import ConnectorRegistry, get_connector_registry

if TYPE_CHECKING:
    from core.thirdparty import RegisterOfInformation

_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "baselith:connector-vendor")


def vendor_provider_id(vendor: str) -> str:
    """Stable register id for ``vendor`` (case- and whitespace-insensitive)."""
    return uuid.uuid5(_NAMESPACE, " ".join(vendor.lower().split())).hex


async def declare_ict_providers(
    *,
    registry: ConnectorRegistry | None = None,
    register: RegisterOfInformation | None = None,
) -> list[str]:
    """Add a provider for every vendor the registered connectors declare.

    Connectors of the same vendor share one provider. Providers already in
    the register are not modified.

    Returns:
        The provider ids, one per distinct vendor, in registration order.
    """
    from core.thirdparty import ICTProvider, get_register

    target = register or get_register()
    ids: list[str] = []
    for spec in (registry or get_connector_registry()).specs():
        if not spec.vendor:
            continue
        provider_id = vendor_provider_id(spec.vendor)
        if provider_id in ids:
            continue
        ids.append(provider_id)
        if await target.get_provider(provider_id) is not None:
            continue
        await target.register_provider(
            ICTProvider(
                id=provider_id,
                name=spec.vendor,
                lei=spec.vendor_lei,
                country=spec.vendor_country,
            )
        )
    return ids


__all__ = ["declare_ict_providers", "vendor_provider_id"]
