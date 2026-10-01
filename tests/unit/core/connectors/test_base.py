"""BaseConnector: credentials, health, lifecycle, protocol conformance."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from core.connectors import (
    ActionResult,
    ActionSpec,
    BaseConnector,
    Connector,
    ConnectorAuthError,
    ConnectorCapability,
    ConnectorConfigError,
    ConnectorSpec,
    ConnectorTransientError,
    CredentialField,
    HealthState,
    SupportsAction,
    SupportsLookup,
    SupportsSync,
)


class Acme(BaseConnector):
    spec = ConnectorSpec(
        name="acme_base",
        display_name="Acme",
        capabilities=frozenset({ConnectorCapability.LOOKUP}),
        credentials=(
            CredentialField("api_key"),
            CredentialField("region", secret=False, required=False),
        ),
        allowed_hosts=frozenset({"api.acme.test"}),
        retry_base_delay=0.0,
        max_attempts=1,
    )

    async def probe(self) -> None:
        await self.http.request(
            "GET",
            "https://api.acme.test/me",
            headers={"Authorization": f"Bearer {self.credential('api_key')}"},
        )

    async def lookup(self, key: str) -> Mapping[str, Any] | None:
        resp = await self.http.request("GET", f"https://api.acme.test/items/{key}")
        return resp.json()


def _acme(*responses: httpx.Response | Exception, key: str | None = "k-123") -> Acme:
    creds = {"api_key": SecretStr(key)} if key else {}
    handler_responses = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        item = (
            handler_responses.pop(0)
            if len(handler_responses) > 1
            else handler_responses[0]
        )
        if isinstance(item, Exception):
            raise item
        return item

    return Acme(credentials=creds, transport=httpx.MockTransport(handler))


async def test_credential_unwraps_the_secret():
    assert _acme(httpx.Response(200)).credential("api_key") == "k-123"


async def test_missing_credential_raises_config_error():
    connector = _acme(httpx.Response(200), key=None)
    assert connector.missing_credentials() == ["api_key"]
    with pytest.raises(ConnectorConfigError, match="api_key"):
        connector.credential("api_key")


async def test_health_unconfigured_without_required_credentials():
    health = await _acme(httpx.Response(200), key=None).health()
    assert health.state is HealthState.UNCONFIGURED
    assert "api_key" in health.detail


async def test_health_ok_when_probe_succeeds():
    connector = _acme(httpx.Response(200))
    health = await connector.health()
    assert health.state is HealthState.OK
    assert health.latency_ms is not None
    await connector.aclose()


async def test_health_down_on_rejected_credentials_and_redacted():
    connector = _acme(httpx.Response(401, text="invalid token k-123"))
    health = await connector.health()
    assert health.state is HealthState.DOWN
    assert "k-123" not in health.detail
    await connector.aclose()


async def test_health_degraded_on_transient_failure():
    connector = _acme(httpx.Response(503))
    health = await connector.health()
    assert health.state is HealthState.DEGRADED
    await connector.aclose()


async def test_health_ok_without_probe_override():
    class NoProbe(BaseConnector):
        spec = ConnectorSpec(name="noprobe", display_name="No probe")

    assert (await NoProbe().health()).state is HealthState.OK


async def test_errors_from_calls_are_typed_and_redacted():
    connector = _acme(httpx.Response(403, text="token k-123 revoked"))
    with pytest.raises(ConnectorAuthError) as info:
        await connector.lookup("x")
    assert "k-123" not in str(info.value)
    await connector.aclose()


async def test_async_context_manager_closes_the_owned_client():
    connector = _acme(httpx.Response(200, json={"id": "x"}))
    async with connector as c:
        assert await c.lookup("x") == {"id": "x"}
        client = c.http.client
    assert client.is_closed


def test_repr_never_contains_credentials():
    assert "k-123" not in repr(_acme(httpx.Response(200)))


def test_subclass_without_spec_is_rejected():
    with pytest.raises(TypeError, match="spec"):

        class Broken(BaseConnector):
            pass


async def test_protocol_conformance():
    connector = _acme(httpx.Response(200))
    assert isinstance(connector, Connector)
    assert isinstance(connector, SupportsLookup)
    assert not isinstance(connector, SupportsSync)
    assert not isinstance(connector, SupportsAction)


async def test_action_connector_satisfies_supports_action():
    class Doer(BaseConnector):
        spec = ConnectorSpec(
            name="doer",
            display_name="Doer",
            capabilities=frozenset({ConnectorCapability.ACTION}),
            actions=(ActionSpec("ping", "Ping", category="read_only"),),
        )

        async def invoke(self, action: str, params: Mapping[str, Any]) -> ActionResult:
            return ActionResult(ok=True)

    assert isinstance(Doer(), SupportsAction)


async def test_transient_errors_propagate_from_calls():
    connector = _acme(httpx.ConnectError("down"))
    with pytest.raises(ConnectorTransientError):
        await connector.lookup("x")
    await connector.aclose()
