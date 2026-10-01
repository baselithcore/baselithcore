"""Credential resolution for connectors.

``CredentialResolver`` is the seam: given a spec and a tenant, return the
credentials as ``SecretStr``. The default, :class:`SecretsCredentialResolver`,
goes through :func:`core.security.secrets.get_secret`, so env vars, ``*_FILE``
files, a secrets directory and any registered Vault backend all work, and
the plugin secret guard still applies. A persistent per-tenant store
registers itself with :func:`set_credential_resolver`.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import SecretStr

from core.connectors.types import ConnectorSpec
from core.security.secrets import get_secret

_UNSAFE_TENANT_CHAR = re.compile(r"[^a-z0-9]")


@runtime_checkable
class CredentialResolver(Protocol):
    """Resolves a connector's credentials for one tenant."""

    async def resolve(
        self, spec: ConnectorSpec, *, tenant_id: str
    ) -> dict[str, SecretStr]:
        """Return the credentials that are set; absent fields are omitted."""
        ...


def _tenant_segment(tenant_id: str) -> str:
    """Encode a tenant id into a secret-name segment, losslessly.

    Lower-case letters and digits pass through upper-cased, so a slug tenant
    stays readable (``acme`` -> ``ACME``). Every other character becomes
    ``_XX_`` with its hex code (``tenant-a`` -> ``TENANT_2D_A``), so
    ``tenant-a``, ``tenant_a`` and ``Tenant-A`` can never share credentials.
    """
    return _UNSAFE_TENANT_CHAR.sub(
        lambda m: "_" + "".join(f"{b:02X}" for b in m.group().encode()) + "_",
        tenant_id,
    ).upper()


def secret_names(spec: ConnectorSpec, field: str, tenant_id: str) -> list[str]:
    """Secret names tried for ``field``, most specific first.

    ``CONNECTOR_<NAME>__<TENANT>__<FIELD>`` then ``CONNECTOR_<NAME>__<FIELD>``.
    Connector and field names are identifiers without ``__`` (enforced by
    their specs) and the tenant segment is encoded losslessly, so distinct
    (connector, tenant, field) triples never map to the same name.
    """
    base = f"CONNECTOR_{spec.name.upper()}"
    names = [f"{base}__{field.upper()}"]
    if tenant_id:
        names.insert(0, f"{base}__{_tenant_segment(tenant_id)}__{field.upper()}")
    return names


class SecretsCredentialResolver:
    """Default resolver backed by the configured secrets provider."""

    async def resolve(
        self, spec: ConnectorSpec, *, tenant_id: str
    ) -> dict[str, SecretStr]:
        # A Vault-style provider may block on the network: keep it off the
        # loop. ``to_thread`` copies the context, so the plugin secret guard
        # still sees the calling plugin.
        return await asyncio.to_thread(self._resolve_sync, spec, tenant_id)

    @staticmethod
    def _resolve_sync(spec: ConnectorSpec, tenant_id: str) -> dict[str, SecretStr]:
        resolved: dict[str, SecretStr] = {}
        for field in spec.credentials:
            for name in secret_names(spec, field.name, tenant_id):
                value = get_secret(name)
                if value is not None and value.get_secret_value():
                    resolved[field.name] = value
                    break
        return resolved


class StaticCredentialResolver:
    """In-memory resolver: ``{connector_name: {field: value}}``.

    For tests and for callers that already hold the credentials. Fields the
    spec does not declare are dropped.
    """

    def __init__(
        self, credentials: Mapping[str, Mapping[str, str | SecretStr]]
    ) -> None:
        self._credentials = {
            name: {
                key: v if isinstance(v, SecretStr) else SecretStr(v)
                for key, v in fields.items()
            }
            for name, fields in credentials.items()
        }

    async def resolve(
        self, spec: ConnectorSpec, *, tenant_id: str
    ) -> dict[str, SecretStr]:
        del tenant_id
        declared = {f.name for f in spec.credentials}
        fields = self._credentials.get(spec.name, {})
        return {k: v for k, v in fields.items() if k in declared}


_resolver: CredentialResolver | None = None


def get_credential_resolver() -> CredentialResolver:
    """The process-wide resolver (the secrets-backed one by default)."""
    global _resolver
    if _resolver is None:
        _resolver = SecretsCredentialResolver()
    return _resolver


def set_credential_resolver(resolver: CredentialResolver | None) -> None:
    """Install a resolver; ``None`` restores the default."""
    global _resolver
    _resolver = resolver


def credential_status(
    spec: ConnectorSpec, credentials: Mapping[str, SecretStr]
) -> dict[str, Any]:
    """UI-safe configuration snapshot. It never contains a value."""

    def is_set(name: str) -> bool:
        value = credentials.get(name)
        return bool(value and value.get_secret_value())

    fields = {
        f.name: {"set": is_set(f.name), "secret": f.secret, "required": f.required}
        for f in spec.credentials
    }
    missing = [f.name for f in spec.credentials if f.required and not is_set(f.name)]
    return {"configured": not missing, "missing": missing, "fields": fields}


__all__ = [
    "CredentialResolver",
    "SecretsCredentialResolver",
    "StaticCredentialResolver",
    "credential_status",
    "get_credential_resolver",
    "secret_names",
    "set_credential_resolver",
]
