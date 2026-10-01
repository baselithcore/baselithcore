"""``BaseConnector``: optional convenience base for concrete connectors.

The protocols in :mod:`core.connectors.protocols` are the contract; this
class only removes the boilerplate every connector otherwise rewrites:
credential access, a lazily built :class:`ConnectorHttp` that redacts those
credentials, a health check that distinguishes "not configured" from "down",
and owned-client lifecycle.

It deliberately defines none of the capability methods, so ``isinstance``
against ``SupportsLookup`` & co. reflects what the subclass really provides.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from types import TracebackType
from typing import Any, ClassVar, Self

import httpx
from pydantic import SecretStr

from core.connectors.errors import (
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorError,
)
from core.connectors.http import ConnectorHttp
from core.connectors.types import ConnectorHealth, ConnectorSpec, HealthState


class BaseConnector:
    """Base class for connectors that talk HTTP.

    Subclasses set the ``spec`` class attribute; an intermediate base that
    has none yet declares itself with ``class Base(BaseConnector, abstract=True)``.

    Args:
        credentials: Resolved credentials, keyed by ``CredentialField.name``.
        client: Injected ``httpx.AsyncClient`` (never closed here).
        transport: Inner transport for the owned client (tests).
    """

    spec: ClassVar[ConnectorSpec]

    def __init_subclass__(cls, abstract: bool = False, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not abstract and not isinstance(getattr(cls, "spec", None), ConnectorSpec):
            raise TypeError(
                f"{cls.__name__} must define a 'spec: ConnectorSpec' class "
                "attribute (or pass abstract=True)"
            )

    def __init__(
        self,
        *,
        credentials: Mapping[str, SecretStr] | None = None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._credentials: dict[str, SecretStr] = dict(credentials or {})
        self._client = client
        self._transport = transport
        self._http: ConnectorHttp | None = None

    @property
    def name(self) -> str:
        """The connector's registry name."""
        return self.spec.name

    @property
    def http(self) -> ConnectorHttp:
        """Shared HTTP client, built on first use."""
        if self._http is None:
            self._http = ConnectorHttp(
                self.spec,
                secrets=self.secret_values(),
                client=self._client,
                transport=self._transport,
            )
        return self._http

    def secret_values(self) -> list[str]:
        """Plain values of the secret credentials, for redaction only."""
        secret_fields = {f.name for f in self.spec.credentials if f.secret}
        return [
            value.get_secret_value()
            for key, value in self._credentials.items()
            if key in secret_fields
        ]

    def credential(self, name: str) -> str:
        """Return the plain value of credential ``name``.

        Unwrap at the point of use only; never store the result.

        Raises:
            ConnectorConfigError: The credential is not set.
        """
        value = self._credentials.get(name)
        plain = value.get_secret_value() if value is not None else ""
        if not plain:
            raise ConnectorConfigError(self.name, f"credential {name!r} is not set")
        return plain

    def missing_credentials(self) -> list[str]:
        """Names of required credential fields that are not set."""
        return [
            f.name
            for f in self.spec.credentials
            if f.required
            and not (
                (value := self._credentials.get(f.name)) and value.get_secret_value()
            )
        ]

    async def probe(self) -> None:
        """Cheapest authenticated call proving the connector works.

        Override it (e.g. ``GET /me``). The default does nothing, so an
        unprobed connector reports ``ok`` once it is configured.
        """
        return None

    async def health(self) -> ConnectorHealth:
        """Check configuration, then run :meth:`probe` and time it."""
        missing = self.missing_credentials()
        if missing:
            return ConnectorHealth(
                HealthState.UNCONFIGURED,
                detail=f"missing credentials: {', '.join(missing)}",
            )
        started = time.perf_counter()
        try:
            await self.probe()
        except (ConnectorAuthError, ConnectorConfigError) as exc:
            return ConnectorHealth(HealthState.DOWN, detail=str(exc))
        except ConnectorError as exc:
            return ConnectorHealth(HealthState.DEGRADED, detail=str(exc))
        latency = (time.perf_counter() - started) * 1000
        return ConnectorHealth(HealthState.OK, latency_ms=round(latency, 2))

    async def aclose(self) -> None:
        """Close the HTTP client if this connector owns it."""
        if self._http is not None:
            await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} connector={self.spec.name!r}>"


__all__ = ["BaseConnector"]
